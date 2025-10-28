export default function eleventyConfigSetup(eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ "src/assets/images": "images" });
  eleventyConfig.addPassthroughCopy({ "src/assets/css": "css" });
  eleventyConfig.addPassthroughCopy({ "src/assets/js": "js" });
  eleventyConfig.addPassthroughCopy({ "src/assets/style-guide.html": "style-guide.html" });
  eleventyConfig.addPassthroughCopy({ "src/scripts": "js" });

  eleventyConfig.ignores.add("src/assets/style-guide.html");

  eleventyConfig.setServerOptions({
    port: 4173,
    watch: ["src/**/*.{njk,html,js,css,json}"]
  });

  return {
    dir: {
      input: "src",
      includes: "partials",
      data: "data",
      output: "public"
    },
    htmlTemplateEngine: "njk",
    pathPrefix: "/demo"
  };
}
