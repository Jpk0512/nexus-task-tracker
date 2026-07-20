export default function SecretsPage() {
	return (
		<div className="mx-auto flex max-w-2xl flex-col gap-3 px-6 py-12">
			<h1 className="font-[510] text-[22px] tracking-[-0.02em]">Secrets</h1>
			<p className="text-[13px] text-muted-foreground">Infisical bridge — masked secrets, import, inject into MCP env. Never paste into chat.</p>
			<p className="text-[12px] text-muted-foreground/80">
				Shell live — full behavior ships in the next implementation phases.
			</p>
		</div>
	);
}
