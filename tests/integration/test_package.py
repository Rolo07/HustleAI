"""Validate installed package startup and private-data path isolation."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class PackageTests(unittest.TestCase):
    def test_data_directory_from_environment(self):
        """A subprocess resolves runtime paths to the configured directory."""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c',
                'import json; from hustleai.config import ROOT,CONFIG,MAPPING; '
                'print(json.dumps([str(ROOT),str(CONFIG),str(MAPPING)]))'],
                env={**os.environ, 'HUSTLEAI_DATA_DIR': directory},
                capture_output=True, text=True, check=True)
            paths = list(map(Path, json.loads(result.stdout)))
            self.assertEqual(paths[0], Path(directory).resolve())
            self.assertTrue(all(p.parent == paths[0] for p in paths[1:]))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_mcp_discovers_eighteen_tools_without_credentials(self):
        """Owner server can initialize and advertise tools with empty private state."""
        async def check(directory):
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'hustleai.mcp.owner_server'],
                env={'HUSTLEAI_DATA_DIR': directory})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 18)
                    names = {t.name for t in tools}
                    for name in ('approve_invoice_version', 'reorder_forecast', 'set_reorder_cycle', 'exclude_from_forecast', 'send_pdf_to_owner'):
                        self.assertIn(name, names)
                    self.assertTrue(all(t.description for t in tools))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


    def test_mcp_registers_only_enabled_features(self):
        """A tenant with only invoicing enabled gets exactly the 14 invoicing tools."""
        async def check(directory):
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'hustleai.mcp.owner_server'], env={'HUSTLEAI_DATA_DIR': directory})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    self.assertEqual(len(names), 14)
                    self.assertNotIn('reorder_forecast', names)
                    self.assertNotIn('send_pdf_to_owner', names)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'tenant.json').write_text('{"features": ["invoicing"], "owner_name": "Thandi"}')
            asyncio.run(check(directory))


if __name__ == '__main__':
    unittest.main()
