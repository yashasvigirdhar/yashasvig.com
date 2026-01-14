declare module "@pagefind/default-ui" {
	declare class PagefindUI {
		constructor(arg: unknown);
	}
}

declare module "*.jpg" {
	const content: { type: "Buffer"; data: number[] };
	export default content;
}
