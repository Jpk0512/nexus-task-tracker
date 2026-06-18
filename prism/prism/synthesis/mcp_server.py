from __future__ import annotations

import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from prism.config import Config
from prism.genome import BugGenome

mcp = FastMCP("prism")
_genome: BugGenome | None = None


def _get_genome() -> BugGenome:
    global _genome
    if _genome is None:
        _genome = BugGenome(Config.from_env().genome_path)
    return _genome


def _validate_path(file_path: str) -> str:
    """Resolve path and ensure it's under cwd or a safe prefix."""
    resolved = os.path.realpath(file_path)
    safe_prefix = os.path.realpath(os.getcwd())
    if not resolved.startswith(safe_prefix + os.sep) and resolved != safe_prefix:
        raise ValueError(f"Path '{file_path}' is outside the safe working directory")
    return resolved


@mcp.tool()
async def get_convergence_report() -> str:
    genome = _get_genome()
    results, _ = genome.client.scroll(
        collection_name="bug_patterns",
        limit=1000,
        with_payload=True,
    )
    by_location: dict[str, dict[str, Any]] = {}
    for r in results:
        if r.payload is None:
            continue
        key = f"{r.payload.get('file', '')}:{r.payload.get('line', 0)}"
        if key not in by_location:
            by_location[key] = {
                "file": r.payload.get("file", ""),
                "line": r.payload.get("line", 0),
                "techniques": set(),
                "max_severity": 0,
            }
        by_location[key]["techniques"].add(r.payload.get("technique", "unknown"))
        by_location[key]["max_severity"] = max(
            by_location[key]["max_severity"], r.payload.get("severity", 0)
        )

    converged = [v for v in by_location.values() if len(v["techniques"]) >= 2]
    if not converged:
        return "No convergence findings (no location flagged by >=2 techniques)."

    lines = ["| file | line | techniques | severity |", "|------|------|------------|----------|"]
    for item in sorted(converged, key=lambda x: x["max_severity"], reverse=True):
        techs = ", ".join(sorted(item["techniques"]))
        lines.append(f"| {item['file']} | {item['line']} | {techs} | {item['max_severity']} |")
    return "\n".join(lines)


@mcp.tool()
async def get_risk_map() -> str:
    genome = _get_genome()
    top = await genome.get_highest_risk(limit=20)
    if not top:
        return "No risk data available."
    lines = ["| file | line | severity |", "|------|------|----------|"]
    for item in top:
        lines.append(f"| {item.get('file', '')} | {item.get('line', 0)} | {item.get('severity', 0)} |")
    return "\n".join(lines)


@mcp.tool()
async def get_recent_findings(minutes: int = 60) -> str:
    genome = _get_genome()
    cutoff = time.time() - minutes * 60
    results, _ = genome.client.scroll(
        collection_name="bug_patterns",
        limit=1000,
        with_payload=True,
    )
    recent = [r.payload for r in results if r.payload and r.payload.get("ts", 0) >= cutoff]
    if not recent:
        return f"No findings in the last {minutes} minutes."
    lines = [f"**{len(recent)} findings in last {minutes}min**\n"]
    for item in sorted(recent, key=lambda x: x.get("ts", 0), reverse=True):
        lines.append(
            f"- [{item.get('technique', '?')}] {item.get('file', '')}:{item.get('line', 0)} "
            f"sev={item.get('severity', 0)} -- {item.get('description', '')[:80]}"
        )
    return "\n".join(lines)


@mcp.tool()
async def trigger_deep_scan(file_path: str) -> str:
    from prism.sensor.pipeline import AssemblyLine

    genome = _get_genome()
    pipeline = AssemblyLine(genome)

    try:
        file_path = _validate_path(file_path)
    except ValueError as e:
        return f"Access denied: {e}"

    try:
        with open(file_path) as f:
            source = f.read()
    except OSError as exc:
        return f"Cannot read {file_path}: {exc}"

    lines = source.splitlines()
    findings: list[str] = []
    chunk_size = 30
    for i in range(0, len(lines), chunk_size):
        chunk = "\n".join(lines[i : i + chunk_size])
        result = await pipeline.process(chunk, file=file_path, line=i + 1)
        if result.suspicious:
            findings.append(
                f"Line ~{result.line}: [{result.category}] sev={result.severity} {result.root_cause}"
            )

    if not findings:
        return f"No issues found in {file_path}."
    return f"**{len(findings)} finding(s) in {file_path}:**\n" + "\n".join(findings)


async def start_mcp() -> None:
    await mcp.run_stdio_async()
