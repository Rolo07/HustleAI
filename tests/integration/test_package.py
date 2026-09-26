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

    def test_mcp_discovers_seventeen_tools_without_credentials(self):
        """Owner server can initialize and advertise tools with empty private state."""
        async def check(directory):
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'hustleai.mcp.owner_server'],
                env={'HUSTLEAI_DATA_DIR': directory})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 17)
                    names = {t.name for t in tools}
                    for name in ('approve_invoice_version', 'reorder_forecast', 'set_reorder_cycle', 'exclude_from_forecast'):
                        self.assertIn(name, names)
                    self.assertTrue(all(t.description for t in tools))
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
