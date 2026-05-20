import { describe, test, expect, expectTypeOf } from "vitest";
import flag, { flags } from "./release/flag";
import { InvalidOptionError, main, parse } from "./release";

describe(parse.name, () => {
  describe("positional", () => {
    test("empty argv → empty values & positional", () => {
      expect(parse([], [])).toEqual({ values: {}, positional: [] });
    });

    test("non-flag args become positional", () => {
      expect(parse(["a", "b", "c"], [])).toEqual({
        values: {},
        positional: ["a", "b", "c"],
      });
    });

    test("-- separator pushes subsequent args (including flag-like) to positional", () => {
      const out = parse(
        ["--verbose", "--", "--verbose", "x"],
        [flag("verbose", "Enable verbose output", false)],
      );
      expect(out.values).toEqual({ verbose: true });
      expect(out.positional).toEqual(["--verbose", "x"]);
    });

    test("unknown flags are silently skipped", () => {
      expect(parse(["--unknown", "value", "pos"], [])).toEqual({
        values: {},
        positional: ["value", "pos"],
      });
    });
  });

  describe("boolean", () => {
    const verbose = flag(["verbose", "v"], "Enable verbose output", false);

    test("--longform → true", () => {
      expect(parse(["--verbose"], [verbose]).values).toEqual({ verbose: true });
    });

    test("-shorthand → true", () => {
      expect(parse(["-v"], [verbose]).values).toEqual({ verbose: true });
    });

    test("default negation --no-<longform> → false", () => {
      expect(parse(["--no-verbose"], [verbose]).values).toEqual({
        verbose: false,
      });
    });

    test("custom negation longform", () => {
      const f = flag("verbose", "Enable verbose output", false, "concise");
      expect(parse(["--concise"], [f]).values).toEqual({ verbose: false });
    });

    test("custom negation with shorthand", () => {
      const f = flag(["verbose", "v"], "Enable verbose output", false, [
        "no-verbose",
        "V",
      ]);
      expect(parse(["-V"], [f]).values).toEqual({ verbose: false });
    });

    test("last occurrence wins", () => {
      expect(
        parse(["--verbose", "--no-verbose", "--verbose"], [verbose]).values,
      ).toEqual({ verbose: true });
    });

    test("inline value throws", () => {
      expect(() => parse(["--verbose=true"], [verbose])).toThrow(
        /does not take a value/,
      );
    });

    test("not present → omitted (parse does not apply defaults)", () => {
      expect(parse([], [verbose]).values).toEqual({});
    });
  });

  describe("string / number values", () => {
    const output = flag(["output", "o"], "Output file path");
    const timeout = flag("timeout", "Request timeout ms", 0);

    test("space-separated value does not leak into positional", () => {
      expect(parse(["--output", "dist", "pos"], [output])).toEqual({
        values: { output: "dist" },
        positional: ["pos"],
      });
    });

    test("inline =value", () => {
      expect(parse(["--output=dist", "pos"], [output])).toEqual({
        values: { output: "dist" },
        positional: ["pos"],
      });
    });

    test("shorthand", () => {
      expect(parse(["-o", "dist", "pos"], [output])).toEqual({
        values: { output: "dist" },
        positional: ["pos"],
      });
    });

    test("missing value at end of argv → flag skipped", () => {
      expect(parse(["--output"], [output])).toEqual({
        values: {},
        positional: [],
      });
    });

    test("next token starts with `-` → value is not consumed", () => {
      const out = parse(["--output", "--other"], [output]);
      expect(out.values).toEqual({});
    });

    test("number flag parses via Number()", () => {
      expect(parse(["--timeout", "1500"], [timeout]).values).toEqual({
        timeout: 1500,
      });
    });

    test("number flag throws on NaN input", () => {
      expect(() => parse(["--timeout", "nope"], [timeout])).toThrow(
        /expected a number/,
      );
    });

    test("later value overwrites earlier (single-value)", () => {
      expect(parse(["--output", "a", "--output=b"], [output]).values).toEqual({
        output: "b",
      });
    });
  });

  describe("multiple", () => {
    const include = flags(["include", "i"], "Paths to include");
    const ports = flags("port", "Bind port", 8080);

    test("zero occurrences → empty array", () => {
      expect(parse([], [include]).values).toEqual({ include: [] });
    });

    test("repeated occurrences accumulate in order", () => {
      expect(
        parse(["-i", "a", "--include=b", "--include", "c"], [include]).values,
      ).toEqual({ include: ["a", "b", "c"] });
    });

    test("numbers accumulate parsed", () => {
      expect(parse(["--port", "1", "--port=2"], [ports]).values).toEqual({
        port: [1, 2],
      });
    });
  });

  describe("options validation", () => {
    const env = flag("env", "Deploy target", ["production", "staging"]);
    const workers = flag("workers", "Thread count", [1, 2, 4, 8]);

    test("valid string option accepted", () => {
      expect(parse(["--env", "staging"], [env]).values).toEqual({
        env: "staging",
      });
    });

    test("valid numeric option accepted", () => {
      expect(parse(["--workers=4"], [workers]).values).toEqual({ workers: 4 });
    });

    test("invalid option throws InvalidOptionError", () => {
      try {
        parse(["--env", "dev"], [env]);
        expect.fail("expected InvalidOptionError");
      } catch (err) {
        expect(err).toBeInstanceOf(InvalidOptionError);
        expect((err as InvalidOptionError).flag).toBe("env");
        expect((err as InvalidOptionError).received).toBe("dev");
        expect((err as InvalidOptionError).options).toEqual([
          "production",
          "staging",
        ]);
      }
    });
  });

  describe("flag map construction", () => {
    test("duplicate longform throws", () => {
      expect(() =>
        parse([], [flag("foo", "first"), flag("foo", "second")]),
      ).toThrow(/Duplicate longform flag: --foo/);
    });

    test("duplicate shorthand throws", () => {
      expect(() =>
        parse([], [flag(["foo", "x"], "first"), flag(["bar", "x"], "second")]),
      ).toThrow(/Duplicate shorthand flag: -x/);
    });
  });

  describe("mixed", () => {
    test("flags, multi, positional, and -- separator together", () => {
      const decls = [
        flag(["verbose", "v"], "verbose", false),
        flag(["output", "o"], "out"),
        flags(["include", "i"], "include"),
      ];
      const out = parse(
        ["pos1", "-v", "--output=dist", "-i", "a", "-i", "b", "--", "-i", "c"],
        decls,
      );
      expect(out.values).toEqual({
        verbose: true,
        output: "dist",
        include: ["a", "b"],
      });
      expect(out.positional).toEqual(["pos1", "-i", "c"]);
    });
  });
});

describe(main.name, () => {
  describe("type ↔ value alignment", () => {
    test("boolean with default → boolean", () => {
      const verboseFlag = flag("verbose", "Enable verbose output", false);
      const present = main(["--verbose"], "desc", [verboseFlag]);
      const absent = main([], "desc", [verboseFlag]);

      expectTypeOf(present.verbose).toEqualTypeOf<boolean>();
      expect(present.verbose).toBe(true);
      expect(absent.verbose).toBe(false); // default applied
    });

    test("string with default → string", () => {
      const outputFlag = flag("output", "Output file path", "./dist");
      const present = main(["--output", "build"], "desc", [outputFlag]);
      const absent = main([], "desc", [outputFlag]);

      expectTypeOf(present.output).toEqualTypeOf<string>();
      expect(present.output).toBe("build");
      expect(absent.output).toBe("./dist");
    });

    test("string without default → string | undefined", () => {
      const outputFlag = flag("output", "Output file path");
      const present = main(["--output=build"], "desc", [outputFlag]);
      const absent = main([], "desc", [outputFlag]);

      expectTypeOf(present.output).toEqualTypeOf<string | undefined>();
      expect(present.output).toBe("build");
      expect(absent.output).toBeUndefined();
    });

    test("number with default → number", () => {
      const timeoutFlag = flag("timeout", "Request timeout ms", 5000);
      const present = main(["--timeout", "250"], "desc", [timeoutFlag]);
      const absent = main([], "desc", [timeoutFlag]);

      expectTypeOf(present.timeout).toEqualTypeOf<number>();
      expect(present.timeout).toBe(250);
      expect(absent.timeout).toBe(5000);
    });

    test("string-enum with default → literal union", () => {
      const envFlag = flag(
        "env",
        "Deploy target",
        ["production", "staging"],
        "staging",
      );
      const present = main(["--env", "production"], "desc", [envFlag]);
      const absent = main([], "desc", [envFlag]);

      expectTypeOf(present.env).toEqualTypeOf<"production" | "staging">();
      expect(present.env).toBe("production");
      expect(absent.env).toBe("staging");
    });

    test("string-enum without default → literal union | undefined", () => {
      const envFlag = flag("env", "Deploy target", ["production", "staging"]);
      const result = main([], "desc", [envFlag]);
      expectTypeOf(result.env).toEqualTypeOf<
        "production" | "staging" | undefined
      >();
      expect(result.env).toBeUndefined();
    });

    test("multi-string → string[]", () => {
      const includeFlag = flags(["include", "i"], "Paths to include");
      const result = main(["-i", "a", "--include=b"], "desc", [includeFlag]);

      expectTypeOf(result.include).toEqualTypeOf<string[]>();
      expect(result.include).toEqual(["a", "b"]);
    });

    test("multi-number with default seed → number[], seeded when absent", () => {
      const portFlag = flags("port", "Bind port", 8080);
      const present = main(["--port", "3000", "--port=4000"], "desc", [
        portFlag,
      ]);
      const absent = main([], "desc", [portFlag]);

      expectTypeOf(present.port).toEqualTypeOf<number[]>();
      expect(present.port).toEqual([3000, 4000]);
      expect(absent.port).toEqual([8080]); // seeded by default
    });

    test("multi-enum → literal union[]", () => {
      const tagFlag = flags("tag", "Filter tag", ["alpha", "beta", "stable"]);
      const result = main(["--tag", "alpha", "--tag=stable"], "desc", [
        tagFlag,
      ]);

      expectTypeOf(result.tag).toEqualTypeOf<("alpha" | "beta" | "stable")[]>();
      expect(result.tag).toEqual(["alpha", "stable"]);
    });

    test("multi-enum with array default → seeds with all defaults when absent", () => {
      const options = ["alpha", "beta", "stable"] as const;
      const seeds: (typeof options)[number][] = ["alpha", "beta"];
      const tagFlag = flags("tag", "Filter tag", options, seeds);

      const absent = main([], "desc", [tagFlag]);
      const present = main(["--tag", "stable"], "desc", [tagFlag]);

      expectTypeOf(absent.tag).toEqualTypeOf<("alpha" | "beta" | "stable")[]>();
      expect(absent.tag).toEqual(["alpha", "beta"]);
      // explicit values drop the seed entirely (matches single-default semantics)
      expect(present.tag).toEqual(["stable"]);
    });

    test("multi-enum array default is copied, not aliased", () => {
      const seeds = ["alpha", "beta"];
      const tagFlag = flags(
        "tag",
        "Filter tag",
        ["alpha", "beta", "stable"],
        seeds as ("alpha" | "beta")[],
      );

      const a = main([], "desc", [tagFlag]);
      const b = main([], "desc", [tagFlag]);

      expect(a.tag).toEqual(["alpha", "beta"]);
      // mutating one parsed result should not affect the next, nor the seed
      (a.tag as string[]).push("stable");
      expect(b.tag).toEqual(["alpha", "beta"]);
      expect(seeds).toEqual(["alpha", "beta"]);
    });

    test("multi-number-enum with array default", () => {
      const options = [1, 2, 4, 8] as const;
      const seeds: (typeof options)[number][] = [1, 2];
      const workersFlag = flags("workers", "Thread count", options, seeds);

      const absent = main([], "desc", [workersFlag]);
      const present = main(["--workers", "8"], "desc", [workersFlag]);

      expectTypeOf(absent.workers).toEqualTypeOf<(1 | 2 | 4 | 8)[]>();
      expect(absent.workers).toEqual([1, 2]);
      expect(present.workers).toEqual([8]);
    });
  });

  describe("multiple flags together", () => {
    test("each key resolves to its own type", () => {
      const result = main(
        ["pos1", "-v", "--output=dist", "--timeout", "100"],
        "desc",
        [
          flag(["verbose", "v"], "verbose", false),
          flag("output", "out"),
          flag("timeout", "timeout ms", 1000),
        ],
      );

      expectTypeOf(result.verbose).toEqualTypeOf<boolean>();
      expectTypeOf(result.output).toEqualTypeOf<string | undefined>();
      expectTypeOf(result.timeout).toEqualTypeOf<number>();

      expect(result.verbose).toBe(true);
      expect(result.output).toBe("dist");
      expect(result.timeout).toBe(100);
    });
  });

  describe("positional", () => {
    test("indexed access via proxy", () => {
      const result = main(["a", "b"], "desc", []);
      expectTypeOf(result[0]).toEqualTypeOf<string | undefined>();
      expect(result[0]).toBe("a");
      expect(result[1]).toBe("b");
      expect(result[2]).toBeUndefined();
    });

    test("iterable yields positional args", () => {
      const result = main(["x", "y", "z"], "desc", []);
      expect([...result]).toEqual(["x", "y", "z"]);
    });
  });
});
