import AvatarImage from "@/assets/avatar.jpg";
import RobotoMonoBold from "@/assets/roboto-mono-700.ttf";
import RobotoMono from "@/assets/roboto-mono-regular.ttf";
import { getAllPosts } from "@/data/post";
import { siteConfig } from "@/site.config";
import { getFormattedDate } from "@/utils/date";
import { Resvg } from "@resvg/resvg-js";
import type { APIContext, InferGetStaticPropsType } from "astro";
import satori, { type SatoriOptions } from "satori";
import { html } from "satori-html";

const ogOptions: SatoriOptions = {
	// debug: true,
	fonts: [
		{
			data: Buffer.from(RobotoMono),
			name: "Roboto Mono",
			style: "normal",
			weight: 400,
		},
		{
			data: Buffer.from(RobotoMonoBold),
			name: "Roboto Mono",
			style: "normal",
			weight: 700,
		},
	],
	height: 630,
	width: 1200,
};

const markup = (title: string, pubDate: string, avatarBase64: string) =>
	html`<div tw="flex flex-col w-full h-full bg-[#1d1f21] text-[#c9cacc]">
		<div tw="flex flex-1 w-full p-10">
			<div tw="flex flex-col flex-1 justify-center pr-8">
				<p tw="text-2xl mb-6">${pubDate}</p>
				<h1 tw="text-5xl font-bold leading-snug text-white">${title}</h1>
			</div>
			<div tw="flex items-center justify-center w-64">
				<img src="${avatarBase64}" tw="w-40" />
			</div>
		</div>
		<div tw="flex items-center justify-end w-full p-10 border-t border-[#2bbc89] text-xl">
			<p>${siteConfig.author}</p>
		</div>
	</div>`;

type Props = InferGetStaticPropsType<typeof getStaticPaths>;

export async function GET(context: APIContext) {
	const { pubDate, title } = context.props as Props;

	const postDate = getFormattedDate(pubDate, {
		month: "long",
		weekday: "long",
	});

	const avatarBase64 = `data:image/jpeg;base64,${Buffer.from((AvatarImage as unknown as { data: number[] }).data).toString("base64")}`;

	const svg = await satori(markup(title, postDate, avatarBase64), ogOptions);
  const png = new Resvg(svg).render().asPng();
  // Convert Node Buffer to ArrayBuffer for Web Blob compatibility
  // Cast to any to satisfy BodyInit typing differences between DOM/undici in CI
  return new Response(png as any, {
		headers: {
			"Cache-Control": "public, max-age=31536000, immutable",
			"Content-Type": "image/png",
		},
	});
}

export async function getStaticPaths() {
	const posts = await getAllPosts();
	return posts
		.filter(({ data }) => !data.ogImage)
		.map((post) => ({
			params: { slug: post.id },
			props: {
				pubDate: post.data.updatedDate ?? post.data.publishDate,
				title: post.data.title,
			},
		}));
}
