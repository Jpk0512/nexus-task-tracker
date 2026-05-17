"use client";
import { Button } from "@ui/components/ui/button";
import { Check } from "lucide-react";
import type React from "react";

const PricingCard = ({
	tier,
	price,
	features,
	recommended = false,
}: {
	tier: string;
	price: string;
	features: string[];
	recommended?: boolean;
}) => (
	<div
		className={`border p-8 ${recommended ? "border-white/20 bg-white/5" : "border-white/5 bg-transparent"} relative flex flex-col`}
	>
		{recommended && (
			<div className="absolute top-0 right-0 bg-white px-3 py-1 font-bold text-[10px] text-black uppercase tracking-wider">
				Popular
			</div>
		)}
		<h3 className="mb-2 font-medium text-lg text-white">{tier}</h3>
		<div className="mb-6 flex items-baseline gap-1">
			<span className="font-light text-3xl text-white">{price}</span>
			{price !== "Free" && <span className="text-sm text-zinc-500">/mo</span>}
		</div>
		<ul className="mb-8 flex-1 space-y-4">
			{features.map((f, i) => (
				<li key={i} className="flex items-start gap-3 text-sm text-zinc-400">
					<Check size={16} className="mt-0.5 text-white" />
					<span>{f}</span>
				</li>
			))}
		</ul>
		<Button
			variant={recommended ? "default" : "secondary"}
			className="w-full justify-center"
		>
			{recommended ? "Start Free Trial" : "Get Started"}
		</Button>
	</div>
);

export const Pricing: React.FC = () => {
	return (
		<section
			id="pricing"
			className="border-white/5 border-t bg-background py-24"
		>
			<div className="mx-auto max-w-5xl px-6">
				<div className="mb-16 text-center">
					<h2 className="font-light text-3xl text-white">
						Simple, transparent pricing
					</h2>
				</div>
				<div className="mx-auto grid max-w-4xl grid-cols-1 gap-8 md:grid-cols-2">
					<PricingCard
						tier="Starter"
						price="Free"
						features={[
							"Up to 3 team members",
							"Basic task management",
							"Daily digest",
							"1 workspace",
						]}
					/>
					<PricingCard
						tier="Pro Team"
						price="$12"
						recommended={true}
						features={[
							"Unlimited members",
							"Mimrai AI Companion",
							"Advanced analytics",
							"Unlimited workspaces",
							"Priority support",
						]}
					/>
				</div>
			</div>
		</section>
	);
};
