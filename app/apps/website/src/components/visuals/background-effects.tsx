"use client";
import type React from "react";
import { useEffect, useRef, useState } from "react";

export const ParticleNetwork: React.FC = () => {
	const canvasRef = useRef<HTMLCanvasElement>(null);

	useEffect(() => {
		const canvas = canvasRef.current;
		if (!canvas) return;
		const ctx = canvas.getContext("2d");
		if (!ctx) return;

		let width = window.innerWidth;
		let height = window.innerHeight;

		const resize = () => {
			width = window.innerWidth;
			height = window.innerHeight;
			canvas.width = width;
			canvas.height = height;
		};

		window.addEventListener("resize", resize);
		resize();

		const particles: {
			x: number;
			y: number;
			vx: number;
			vy: number;
			size: number;
		}[] = [];
		const particleCount = width < 768 ? 40 : 80;

		for (let i = 0; i < particleCount; i++) {
			particles.push({
				x: Math.random() * width,
				y: Math.random() * height,
				vx: (Math.random() - 0.5) * 0.2,
				vy: (Math.random() - 0.5) * 0.2,
				size: Math.random() * 1.5 + 0.5,
			});
		}

		const animate = () => {
			ctx.clearRect(0, 0, width, height);

			// Draw connecting lines
			ctx.strokeStyle = "rgba(255, 255, 255, 0.03)";
			ctx.lineWidth = 1;

			for (let i = 0; i < particles.length; i++) {
				const p1 = particles[i]!;
				p1.x += p1.vx;
				p1.y += p1.vy;

				if (p1.x < 0 || p1.x > width) p1.vx *= -1;
				if (p1.y < 0 || p1.y > height) p1.vy *= -1;

				// Draw particle
				ctx.fillStyle = "rgba(255, 255, 255, 0.2)";
				ctx.beginPath();
				ctx.arc(p1.x, p1.y, p1.size, 0, Math.PI * 2);
				ctx.fill();

				// Connect
				for (let j = i + 1; j < particles.length; j++) {
					const p2 = particles[j]!;
					const dx = p1.x - p2.x;
					const dy = p1.y - p2.y;
					const dist = Math.sqrt(dx * dx + dy * dy);

					if (dist < 150) {
						ctx.beginPath();
						ctx.moveTo(p1.x, p1.y);
						ctx.lineTo(p2.x, p2.y);
						ctx.stroke();
					}
				}
			}
			requestAnimationFrame(animate);
		};

		animate();

		return () => window.removeEventListener("resize", resize);
	}, []);

	return (
		<canvas
			ref={canvasRef}
			className="pointer-events-none absolute inset-0 z-0"
		/>
	);
};

export const AbstractSphere: React.FC = () => {
	return null;
};

export const Waveform: React.FC<{ className?: string }> = ({ className }) => {
	const [shouldRender, setShouldRender] = useState(false);

	useEffect(() => {
		setShouldRender(true);
	}, []);

	if (!shouldRender) return null;

	return (
		<div className={`flex h-8 items-center gap-1 ${className}`}>
			{[...Array(12)].map((_, i) => (
				<div
					key={i}
					className="w-1 animate-pulse bg-white/20"
					style={{
						height: `${Math.random() * 100}%`,
						animationDelay: `${i * 0.1}s`,
						animationDuration: "1.5s",
					}}
				/>
			))}
		</div>
	);
};
