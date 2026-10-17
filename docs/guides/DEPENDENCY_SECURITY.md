# Dependency security — reviewed 15 October 2026

The project environment now runs **Python 3.14.8** with **pip 26.2.1**. The
previous Python 3.14.0 interpreter and pip 25.2 were outdated. OSV returned six
GitHub advisories (and overlapping PyPA aliases) for pip 25.2:

- [GHSA-4xh5-x5gv-qwph](https://github.com/advisories/GHSA-4xh5-x5gv-qwph)
- [GHSA-58qw-9mgm-455v](https://github.com/advisories/GHSA-58qw-9mgm-455v)
- [GHSA-6vgw-5pg2-w6jp](https://github.com/advisories/GHSA-6vgw-5pg2-w6jp)
- [GHSA-jp4c-xjxw-mgf9](https://github.com/advisories/GHSA-jp4c-xjxw-mgf9)
- [GHSA-qwm4-qh6w-59xr](https://github.com/advisories/GHSA-qwm4-qh6w-59xr)
- [GHSA-wf93-45jw-7689](https://github.com/advisories/GHSA-wf93-45jw-7689)

The update also addresses Python security fixes described in the official
[3.14.8 release notes](https://www.python.org/downloads/release/python-3148/).
The patched runtime is installed through Homebrew; the original system/framework
interpreter was not replaced. `.venv` was rebuilt against the patched runtime.
A rollback copy of the previous environment remains in ignored
`var/security/venv-before-python-3.14.8`; it is not the active environment.

## Audit result and scope

[Machine-readable result](../security/dependency-audit-2026-10-15.json):
**39 exact pinned Python packages, zero known advisory matches and zero fully
yanked releases**, checked against OSV and PyPI release vulnerability metadata.
This includes all installed dependencies, direct/transitive optional PDF,
PostgreSQL and Hermes packages, pip, and setuptools. The runtime passed the
reviewed patch-floor check. The PDF stack includes pdfplumber 0.11.10,
pdfminer.six 20260107, Pillow 12.3.0 and pypdfium2 5.14.0.

The expanded preflight checked 49 package/version combinations, including a newer
resolver proposal. The final installation deliberately retains already-audited,
compatible versions for unaffected packages. Homebrew installed the patched
runtime and its formula dependencies, including OpenSSL 4.0.3. CI action releases
were checked against OSV's GitHub Actions ecosystem (no returned advisories) and
pinned to official commit SHAs: checkout v7.0.1 and setup-python v7.0.0.

A clean result means no matching *known, published* advisories in these sources at
that time. It does not prove the absence of undisclosed issues, malicious packages,
or every vulnerability in bundled native libraries. This is not a complete macOS,
Homebrew-library, browser, VPS Hermes, infrastructure or application-code audit.
The VPS was not accessed or updated. There is no npm dependency tree or external
JavaScript framework in this dashboard.

## Reproduce the checks

Use the existing patched virtual environment:

```sh
.venv/bin/python -m pip check
.venv/bin/python scripts/audit_dependencies.py --installed --lock requirements-lock.txt --check-runtime --output var/security/audit.json
.venv/bin/python -m unittest discover -s tests -q
```

The auditor queries OSV batches with pagination and PyPI's version-specific JSON
API. Only public package names and versions leave the machine. It does not read
`.env`, bank statements, customer records or credentials. Failed/partial advisory
or registry responses, unsupported unpinned entries, unverifiable releases,
yanked releases and vulnerability matches cause a nonzero exit. Network failure
must never be interpreted as no vulnerabilities.

The editable first-party HustleAI package is excluded from registry lookup because
it is local project source. Its behavior is covered by the project tests, not by
pretending a public PyPI package represents this checkout.

## Audited installation

`requirements-lock.txt` is an exact version **constraints** file for all supported
extras and tooling. Select only the extras needed; `-c` does not install unused
extras. Versions are pinned, but this is not a cross-platform artifact hash lock.
Build isolation is disabled after installing the exact reviewed build backend:

```sh
# On this Mac, explicitly select Homebrew's patched interpreter:
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
.venv/bin/python -m pip install -c requirements-lock.txt --upgrade pip setuptools
.venv/bin/python -m pip install --no-build-isolation -c requirements-lock.txt -e '.[bank,postgres,hermes]'
```

Linux/other hosts must use their own patched interpreter. Resolving all pins for
Linux x86-64 / Python 3.11 succeeded without source builds; actual tests were run
locally on macOS / Python 3.14.8. Cross-platform resolution is not execution of the
Linux tests. Do not overwrite an existing environment without a backup and
stopping its workers.

## Ongoing checks

- `scripts/python_security_policy.json` records reviewed minimum patch releases:
  3.11.17, 3.12.15, 3.13.16 and 3.14.8. Unknown minor series fail closed until
  reviewed. This policy must advance when security releases arrive; it is not a
  live CPython vulnerability feed. `.python-version` selects 3.14.8 for tools that
  honor that file.
- `.github/workflows/security.yml` is configured for push, pull request, manual
  runs and weekly checks. It audits the pins, installed tooling, runtime floor and
  actual resolved optional dependency tree. Registry outages also fail the job.
- The test workflow installs with audited constraints on Python 3.11 and 3.14,
  using the latest available patch for each series. Action references are full
  commit SHAs and checkout does not persist credentials.
- Dependabot configuration requests weekly pip and GitHub Actions update PRs.
  Updates are never auto-merged. Re-resolve constraints, audit, run tests and
  review changed packages before accepting an update.

These workflow/configuration changes are local until committed and pushed. No
GitHub-hosted run or Dependabot PR is claimed in this local verification.

References: [OSV API](https://google.github.io/osv.dev/post-v1-querybatch/),
[PyPI JSON API](https://docs.pypi.org/api/json/), and
[official Python security releases](https://blog.python.org/2026/10/python-31022-31117/).

## Validation after the update

- 199 tests run: 163 passed, 36 skipped (opt-in external integration suites).
- A real, fictional PDF table passed extraction through the installed parser's
  subprocess, with the expected date/reference/credit preserved.
- `pip check`, YAML parsing, JavaScript syntax and Git whitespace checks passed.
- Both local health endpoints returned 200. Live owner authentication, private
  report reads and the Zoho catalog were checked after restarting the dashboard.
- No real bank statement, customer payment write, report email or customer
  WhatsApp send was used for this dependency verification.
