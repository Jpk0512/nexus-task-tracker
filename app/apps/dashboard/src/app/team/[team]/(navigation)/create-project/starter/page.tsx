/**
 * Thin Starter shell — full workshop (FEAT-003) lands after Attention + Capture + Project Place.
 */
export default function StarterWorkshopPage() {
	return (
		<div className="mx-auto flex max-w-2xl flex-col gap-4 px-6 py-12">
			<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
				Project Starter
			</h1>
			<p className="text-[13px] text-muted-foreground">
				Workshop UI is scaffolding in. Entry points (this page + Home Continue
				card) ship first; concept → handoff phases connect next.
			</p>
			<div className="rounded-xl border border-border/60 bg-card/40 p-5 text-[13px]">
				<p className="font-[510]">Planned phases</p>
				<ol className="mt-2 list-inside list-decimal space-y-1 text-muted-foreground">
					<li>Seed — name, directory, drivers</li>
					<li>Concept — grill-with-docs</li>
					<li>Architecture — wayfinder map</li>
					<li>UX — prototype gallery</li>
					<li>Plan + Handoff</li>
					<li>Board materialize + execute</li>
				</ol>
			</div>
		</div>
	);
}
