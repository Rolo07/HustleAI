"""Opt-in PostgreSQL tests in a unique disposable schema, with fake Zoho data.

Run HUSTLEAI_TEST_POSTGRES=1 .venv/bin/python -m unittest
 tests.integration.test_postgres -v. Saved admin settings are read privately.
No real clients, invoices, payments or application tables are mutated.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from hustleai.config import ROOT
from hustleai.storage.postgres.repository import PostgresRepository, connect
from tests.unit.test_zoho_workflows import WorkflowTests, WorkflowAPI
from hustleai.workflows.service import Service


@unittest.skipUnless(os.environ.get('HUSTLEAI_TEST_POSTGRES') == '1', 'Opt-in PostgreSQL tests')
class PostgresTests(WorkflowTests):
    """Run invoice safety regressions against actual PostgreSQL transactions."""

    @classmethod
    def setUpClass(cls):
        from psycopg import sql
        cls.schema = 'hustle_test_' + uuid.uuid4().hex
        cls.admin = connect(ROOT, '.supabase-credentials.json')
        migration = (Path(__file__).parents[2] / 'supabase/migrations/202609260001_workflow_storage.sql').read_text()
        try:
            cls.admin.execute(migration.replace('hustle_private', cls.schema))
            functions = (Path(__file__).parents[2] / 'supabase/migrations/202609260002_workflow_functions.sql').read_text()
            with cls.admin.transaction():
                cls.admin.execute(functions.replace('hustle_private', cls.schema))
                # Scope the existing restricted role to synthetic rows in this
                # disposable schema only. Production policies are untouched.
                cls.admin.execute(f'GRANT USAGE ON SCHEMA {cls.schema} TO hustleai_runtime')
                for table in ('operations', 'phone_mappings', 'invoice_reviews', 'invoice_approvals'):
                    cls.admin.execute(f'GRANT SELECT,INSERT,UPDATE ON {cls.schema}.{table} TO hustleai_runtime')
                    cls.admin.execute(f"CREATE POLICY test_org ON {cls.schema}.{table} TO hustleai_runtime USING (organization_id='123') WITH CHECK (organization_id='123')")
                # The forecast migration copies the operations policy above.
                forecast = (Path(__file__).parents[2] / 'supabase/migrations/202609260003_reorder_forecast.sql').read_text()
                cls.admin.execute(forecast.replace('hustle_private', cls.schema))
        except BaseException:
            cls.admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(cls.schema)))
            cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql
        cls.admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))
        cls.admin.close()

    def repository(self, organization='123'):
        from psycopg import sql
        db = connect(ROOT, '.supabase-credentials.json')
        db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        return PostgresRepository(ROOT, organization, db, self.schema)

    def setUp(self):
        self.admin.execute(f'TRUNCATE {self.schema}.operations, {self.schema}.phone_mappings, {self.schema}.invoice_reviews, {self.schema}.invoice_approvals, {self.schema}.customer_order_cycles, {self.schema}.reorder_forecasts CASCADE')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'organization_id': '123', 'country_code': '27', 'storage_backend': 'postgres'}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        self.api = WorkflowAPI(self.root)
        with patch('hustleai.workflows.service.open_repository', side_effect=lambda *_: self.repository()):
            self.s = Service(self.api, self.root)
        self.s.store.add_mapping('1', '+27837758811')

    def test_expired_review(self):
        r = self.review()
        self.s.db.execute("UPDATE invoice_reviews SET created_at=to_timestamp(0)")
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.approval(r)

    def test_migration_preserves_claims_revokes_approvals_and_resumes(self):
        from hustleai.storage.postgres import migrate
        self.s.db.execute('DELETE FROM phone_mappings')
        self.s.db.execute(f'CREATE TABLE {self.schema}.legacy_imports (organization_id text PRIMARY KEY, source_hash text NOT NULL)')
        source = {
            'operations': [dict(id='operation', org='123', kind='invoice', payload={'amount': 12.5},
                preview={}, created=1234.1234567, status='attempted', result=None)],
            'invoice_reviews': [dict(review_id='review', org='123', invoice_id='2', customer_id='1',
                phone='+27837758811', version='version', invoice={}, pdf_path='/old/review.pdf',
                pdf_hash='hash', created=1234.1234567, status='approved')],
            'invoice_approvals': [dict(approval_id='approval', review_id='review', org='123',
                invoice_id='2', version='version', recipient='+27837758811', created=1234.1234567, status='approved')],
        }
        mappings = [('1', '+27837758811')]
        factory = lambda root, org, connection: PostgresRepository(root, org, connection, self.schema)
        try:
            with patch.object(migrate, 'SCHEMA', self.schema), patch.object(migrate, 'PostgresRepository', side_effect=factory):
                first = migrate.import_records(self.s.db, '123', source, mappings)
                repeated = migrate.import_records(self.s.db, '123', source, mappings)
                self.assertEqual(first, repeated)
                self.assertEqual(self.s.store.get('operations', 'id', 'operation')['status'], 'attempted')
                self.assertEqual(self.s.store.review('review')['status'], 'invalid')
                self.assertEqual(self.s.store.approval('approval')['status'], 'invalid')
                source['operations'][0]['status'] = 'pending'
                with self.assertRaisesRegex(ValueError, 'Source changed'):
                    migrate.import_records(self.s.db, '123', source, mappings)
                self.assertEqual(self.s.store.get('operations', 'id', 'operation')['status'], 'attempted')
        finally:
            self.s.db.execute(f'DROP TABLE {self.schema}.legacy_imports')

    def test_upgrade_is_idempotent_and_checks_applied_files(self):
        import hashlib
        from hustleai.storage.postgres.upgrade import apply_upgrades
        directory = Path(__file__).parents[2] / 'supabase/migrations'
        schema = 'hustle_upgrade_test_' + uuid.uuid4().hex
        initial = directory / '202609260001_workflow_storage.sql'
        try:
            self.admin.execute(initial.read_text().replace('hustle_private', schema))
            self.admin.execute(f'CREATE TABLE {schema}.schema_migrations (version text PRIMARY KEY, checksum text NOT NULL)')
            self.admin.execute(f'INSERT INTO {schema}.schema_migrations VALUES (%s,%s)', (initial.name, hashlib.sha256(initial.read_bytes()).hexdigest()))
            applied = apply_upgrades(self.admin, directory, schema)
            self.assertEqual(applied, ['202609260002_workflow_functions.sql', '202609260003_reorder_forecast.sql'])
            self.assertEqual(apply_upgrades(self.admin, directory, schema), [])
            with tempfile.TemporaryDirectory() as changed:
                for path in directory.glob('*.sql'):
                    (Path(changed) / path.name).write_text(path.read_text() + '\n-- changed after application\n')
                with self.assertRaisesRegex(ValueError, 'changed checksum'):
                    apply_upgrades(self.admin, changed, schema)
        finally:
            self.admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')

    def test_functions_respect_runtime_rls_and_public_cannot_execute(self):
        oid = self.s.proposal('invoice', {}, {})['operation_id']
        foreign = self.repository('456')
        try:
            foreign.create_operation('foreign-operation', 'invoice', {}, {})
        finally:
            foreign.close()
        db = connect(ROOT)  # Actual restricted login, not the admin connection.
        store = PostgresRepository(ROOT, '123', db, self.schema)
        try:
            self.assertEqual(store.claim_operation(oid)['status'], 'attempted')
            store.finish_operation(oid, {'ok': True})
            self.assertEqual(store.claim_operation(oid)['result'], {'ok': True})
            visible = db.execute(f'SELECT DISTINCT organization_id FROM {self.schema}.operations').fetchall()
            self.assertEqual(visible, [('123',)])
            store.organization = '456'
            with self.assertRaisesRegex(ValueError, 'Unknown operation'):
                store.claim_operation('foreign-operation')
            with self.assertRaisesRegex(ValueError, 'Unknown operation'):
                store.claim_operation(oid)
            with self.assertRaisesRegex(ValueError, 'not in the attempted'):
                store.finish_operation(oid, {'wrong_org': True})
            self.assertEqual(self.s.store.get('operations', 'id', oid)['result'], {'ok': True})
        finally:
            store.close()
        functions = self.admin.execute("SELECT p.prosecdef, has_function_privilege('anon',p.oid,'EXECUTE'), has_function_privilege('authenticated',p.oid,'EXECUTE') FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s", (self.schema,)).fetchall()
        self.assertEqual(len(functions), 4)
        self.assertTrue(all(row == (False, False, False) for row in functions))

    def test_forecast_settings_and_reports_are_organization_scoped(self):
        from datetime import date
        from psycopg import errors
        store = self.s.store
        store.set_order_cycle('1', 21, 'Customer')
        store.set_forecast_excluded('1', True, 'Customer')
        store.set_forecast_excluded('9', True, 'Other')
        store.set_forecast_excluded('9', False)
        self.assertEqual(store.order_cycles(), {'1': {'cycle_days': 21, 'excluded': True, 'note': 'Customer'}})
        monday = date(2026, 10, 5)
        for version in (1, 2):
            store.save_forecast(monday, date(2026, 10, 12), date(2026, 10, 19), {'run_date': '2026-10-05', 'v': version})
        self.assertEqual(store.forecast(), {'run_date': '2026-10-05', 'v': 2})
        self.assertEqual(store.forecast(monday)['v'], 2)
        self.assertEqual(self.admin.execute(f'SELECT count(*) FROM {self.schema}.reorder_forecasts').fetchone()[0], 1)
        with self.assertRaises(errors.CheckViolation):
            self.admin.execute(f"INSERT INTO {self.schema}.customer_order_cycles (organization_id,contact_id) VALUES ('123','5')")
        other = self.repository('456')
        try:
            self.assertEqual(other.order_cycles(), {})
            self.assertIsNone(other.forecast())
        finally:
            other.close()
        db = connect(ROOT)  # Restricted runtime login under the copied RLS policy.
        runtime = PostgresRepository(ROOT, '123', db, self.schema)
        try:
            self.assertEqual(runtime.order_cycles()['1']['cycle_days'], 21)
            self.assertEqual(runtime.forecast()['v'], 2)
            runtime.organization = '456'
            with self.assertRaises(errors.InsufficientPrivilege):
                runtime.set_order_cycle('7', 14)
        finally:
            runtime.close()

    def test_database_rejects_expired_claim(self):
        oid = self.s.proposal('invoice', {}, {})['operation_id']
        self.s.db.execute('UPDATE operations SET created_at=to_timestamp(0) WHERE id=%s', (oid,))
        with self.assertRaisesRegex(ValueError, 'Expired'):
            self.s.store.claim_operation(oid)
        self.assertEqual(self.s.store.get('operations', 'id', oid)['status'], 'pending')

    def test_claim_rejects_uncommitted_outer_transaction(self):
        oid = self.s.proposal('invoice', {}, {})['operation_id']
        with self.s.db.transaction():
            with self.assertRaisesRegex(ValueError, 'independent committed'):
                self.s.store.claim_operation(oid)
        self.assertEqual(self.s.store.get('operations', 'id', oid)['status'], 'pending')

    def test_database_rechecks_review_binding_and_expiry(self):
        result = self.review()
        saved = self.s.store.review(result['review_id'])
        for field in ('version', 'phone', 'pdf_hash'):
            changed = dict(saved, **{field: 'changed'})
            with self.assertRaisesRegex(ValueError, 'binding changed'):
                self.s.store.approve_review(changed, uuid.uuid4().hex)
        self.s.db.execute('UPDATE invoice_reviews SET created_at=to_timestamp(0)')
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.s.store.approve_review(saved, uuid.uuid4().hex)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM invoice_approvals').fetchone()[0], 0)

    def test_concurrent_database_approval_returns_same_id(self):
        saved = self.s.store.review(self.review()['review_id'])
        barrier = threading.Barrier(2)
        results = []
        errors = []
        def approve():
            store = self.repository()
            try:
                barrier.wait(timeout=10)
                results.append(store.approve_review(saved, uuid.uuid4().hex))
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                store.close()
        workers = [threading.Thread(target=approve) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_claim_is_atomic_between_sessions(self):
        oid = self.s.proposal('invoice', {}, {})['operation_id']
        outcomes = []
        barrier = threading.Barrier(2)
        def claim():
            store = self.repository()
            try:
                barrier.wait(timeout=10)
                try:
                    store.claim_operation(oid)
                    outcomes.append('claimed')
                except ValueError:
                    outcomes.append('blocked')
            finally:
                store.close()
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=30)
        self.assertEqual(sorted(outcomes), ['blocked', 'claimed'])

    def test_organization_isolation_and_shared_number(self):
        self.s.store.add_mapping('9', '+27837758811')
        self.assertEqual(self.s.store.find_contacts('+27837758811'), ['1', '9'])
        oid = self.s.proposal('invoice', {}, {})['operation_id']
        other = self.repository('456')
        try:
            self.assertEqual(other.find_contacts('+27837758811'), [])
            with self.assertRaises(ValueError): other.claim_operation(oid)
        finally:
            other.close()

    def test_result_and_mapping_roll_back_together(self):
        oid = self.s.proposal('client', {}, {})['operation_id']
        self.s.store.claim_operation(oid)
        from psycopg.errors import CheckViolation
        with self.assertRaises(CheckViolation):
            self.s.store.finish_operation(oid, {'success': True}, ('55', 'bad-phone'))
        record = self.s.store.get('operations', 'id', oid)
        self.assertEqual(record['status'], 'attempted')
        self.assertIsNone(record['result'])
        with self.assertRaises(ValueError): self.s.store.claim_operation(oid)

    def test_advisory_lock_cross_session(self):
        other = self.repository()
        try:
            with self.s.store.invoice_lock('2'):
                with self.assertRaisesRegex(ValueError, 'Another workflow'):
                    with other.named_lock('invoice:2', timeout=0.2):
                        self.fail('Competing lock acquired')
            with other.invoice_lock('2'):
                pass
        finally:
            other.close()

    def test_closed_database_does_not_fall_back(self):
        self.s.close()
        from psycopg import OperationalError
        with self.assertRaises(OperationalError):
            self.s.proposal('invoice', {}, {})
        self.assertFalse((self.root / '.zoho-operations.sqlite3').exists())
        self.assertEqual(self.api.puts, [])


# The imported base is not itself another test collection in this module.
del WorkflowTests
