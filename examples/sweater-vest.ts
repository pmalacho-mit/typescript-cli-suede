import { cli } from "../release";

const browsers = ["chromium", "firefox", "webkit"] as const;

export const defaults = {
  server: `http://localhost:5173`,
  closet: `/`,
  browsers: ["chromium"],
  output: "./fashion-show.md",
} as const;

if (cli.entry(import.meta.url)) {
  const { server, closet, browser, output, test, component, help } = cli(
    "Run the sweater vest report script.",
    cli.flag(
      ["server", "s"],
      "URL where the development server is running.",
      defaults.server,
    ),
    cli.flag(
      ["closet", "c"],
      "Endpoint where Closet.svelte is rendered (relative to the server URL).",
      defaults.closet,
    ),
    cli.flags(
      ["browser", "b"],
      "Which browser(s) to run",
      browsers,
      defaults.browsers,
    ),
    cli.flag(
      ["output", "o"],
      "Output path for the Markdown report. Pass an empty string to skip.",
      defaults.output,
    ),
    cli.flag(
      ["test", "t"],
      "Only run tests whose name or id matches this pattern.",
    ),
    cli.flag(
      ["component", "m"],
      "Only open components whose path matches this pattern.",
    ),
  );

  console.log("Here's what I received:");
  console.log({ server, closet, browser, output, test, component });
  console.log(help());
}
