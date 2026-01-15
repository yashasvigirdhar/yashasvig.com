// Generates Instagram Reel sized images (1080x1920) for blog posts
import AvatarImage from "@/assets/avatar.jpg";
import RobotoMonoBold from "@/assets/roboto-mono-700.ttf";
import RobotoMono from "@/assets/roboto-mono-regular.ttf";
import { getAllPosts } from "@/data/post";
import { siteConfig } from "@/site.config";
import { Resvg } from "@resvg/resvg-js";
import type { APIContext, InferGetStaticPropsType } from "astro";
import satori, { type SatoriOptions } from "satori";
import { html } from "satori-html";

const reelOptions: SatoriOptions = {
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
	height: 1920,
	width: 1080,
};

const markup = (title: string, slug: string, avatarBase64: string) =>
	html`<div tw="flex flex-col w-full h-full bg-[#1d1f21] text-[#c9cacc]">
		<div tw="flex items-center justify-center pt-24">
			<img src="${avatarBase64}" tw="w-48" />
		</div>
		<div tw="flex flex-col flex-1 justify-center items-center text-center px-12 -mt-64">
			<h1 tw="text-8xl font-bold leading-tight text-white">${title}</h1>
			<div tw="flex flex-col items-center mt-12">
				<p tw="text-5xl text-white">New post published at</p>
				<p tw="text-5xl text-[#2bbc89]">yashasvig.com/${slug}</p>
			</div>
		</div>
		<div tw="flex justify-center pb-48">
			<p tw="text-6xl text-white font-bold">More details in caption</p>
		</div>
	</div>`;

type Props = InferGetStaticPropsType<typeof getStaticPaths>;

export async function GET(context: APIContext) {
	const { title, slug } = context.props as Props;

	const avatarBase64 = `data:image/jpeg;base64,${Buffer.from((AvatarImage as unknown as { data: number[] }).data).toString("base64")}`;

	const svg = await satori(markup(title, slug, avatarBase64), reelOptions);
	const png = new Resvg(svg).render().asPng();

	return new Response(png as any, {
		headers: {
			"Cache-Control": "public, max-age=31536000, immutable",
			"Content-Type": "image/png",
		},
	});
}

export async function getStaticPaths() {
	const posts = await getAllPosts();
	return posts.map((post) => ({
		params: { slug: post.id },
		props: {
			title: post.data.title,
			slug: post.id,
		},
	}));
}
