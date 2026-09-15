#!/usr/bin/env python3
"""Toy: speak MCP to slowdit, end to end — the way an agent harness would.

Starts the MCP server (`slowdit serve --mcp --transport stdio`) as a
subprocess, then:
  1. lists the tools (the surface an agent sees),
  2. calls `run` with a built-in toy workload — an in-memory tar.gz whose
     script writes a JUnit XML with 1 passing + 1 failing test (on purpose),
  3. prints the RunResponse the tool returns, layer by layer.

The toy needs no files and no git: the archive is built in memory, so this
is the smallest possible end-to-end exercise of the whole contract
(staging -> pull -> one-shot container -> JUnit parse -> two-layer result).

Run it inside the server image:

    docker exec slowdit python /app/examples/toy.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import tarfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# A standard, bash-carrying image (the executor runs `<image> bash -c ...`).
IMAGE = "docker.io/library/debian:bookworm-slim"

#: The toy workload: writes a JUnit XML with 1 passing + 1 failing test.
#: The failure is on purpose — it proves layer 2 (test_result) reports what
#: happened inside, independently of layer 1 (outcome).
TOY_SCRIPT = """#!/bin/sh
cat > /out/results.xml <<'XML'
<?xml version="1.0"?>
<testsuite name="toy" tests="2" failures="1" errors="0" skipped="0" time="0.01">
  <testcase name="starts" classname="toy" time="0.001"/>
  <testcase name="fails_on_purpose" classname="toy" time="0.001">
    <failure message="intentional: proves the two-layer result reports failures">boom</failure>
  </testcase>
</testsuite>
XML
"""


def toy_archive_b64() -> str:
    """The toy workload as a base64 tar.gz (the `code.archive` payload)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = TOY_SCRIPT.encode()
        info = tarfile.TarInfo("scripts/run_tests.sh")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


async def main() -> None:
    server = StdioServerParameters(
        command="slowdit", args=["serve", "--mcp", "--transport", "stdio"]
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("== tools the agent sees ==")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  {t.name}: {t.description.splitlines()[0]}")

            print("\n== calling `run` (toy archive: 1 pass + 1 fail on purpose) ==")
            result = await session.call_tool(
                "run",
                {
                    "image": IMAGE,
                    "code_archive_b64": toy_archive_b64(),
                    "test_command": "sh scripts/run_tests.sh --junitxml=/out/results.xml",
                },
            )
            body = json.loads(result.content[0].text)
            print(json.dumps(body, indent=2))

            print(f"\nlayer 1 (did slowdit do its job): outcome={body['outcome']} "
                  f"exit_code={body['exit_code']}")
            tr = body.get("test_result")
            if tr:
                failing = ", ".join(f["nodeid"] for f in tr["failing"])
                print(f"layer 2 (what happened inside):  total={tr['total']} "
                      f"failures={tr['failures']} ({failing})")
                print("\nNote: outcome=completed does NOT mean the tests passed — "
                      "that is the two-layer contract at work.")


if __name__ == "__main__":
    asyncio.run(main())
