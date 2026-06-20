const TAG_PALETTE = [
	"#9b8afb",
	"#5e6ad2",
	"#26b5ce",
	"#4cb782",
	"#f2c94c",
	"#f2994a",
	"#eb5757",
	"#bb87fc",
];

export function tagColor(name: string): string {
	let h = 0;
	for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
	return TAG_PALETTE[Math.abs(h) % TAG_PALETTE.length] ?? TAG_PALETTE[0]!;
}
