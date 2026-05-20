import { describe, test, expect, expectTypeOf } from "vitest";
import flag, { flags, type Flag } from "./release/flag";

describe(flag.name, () => {
  describe("boolean", () => {
    const name = "verbose";
    const shorthand = "v";
    const description = "Enable verbose output";
    const negation = { longform: "concise", shorthand: "nv" };

    test("no shorthand, omit negation", () => {
      const result = flag(name, description, false);
      expectTypeOf(result).toEqualTypeOf<
        Flag<boolean, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        default: false,
      });
      expect(flag.is(result, "boolean")).toBe(true);
    });

    test("no shorthand with long negation", () => {
      const result = flag(name, description, false, negation.longform);
      expectTypeOf(result).toEqualTypeOf<
        Flag<boolean, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        default: false,
        negation: { longform: negation.longform },
      });
      expect(flag.is(result, "boolean")).toBe(true);
    });

    test("shorthand, omit negation", () => {
      const result = flag([name, shorthand], description, false);
      expectTypeOf(result).toEqualTypeOf<
        Flag<boolean, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        default: false,
      });
      expect(flag.is(result, "boolean")).toBe(true);
    });

    test("shorthand with long negation", () => {
      const result = flag(
        [name, shorthand],
        description,
        false,
        negation.longform,
      );
      expectTypeOf(result).toEqualTypeOf<
        Flag<boolean, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        default: false,
        negation: { longform: negation.longform },
      });
      expect(flag.is(result, "boolean")).toBe(true);
    });

    test("shorthand with long negation", () => {
      const result = flag([name, shorthand], description, false, [
        negation.longform,
        negation.shorthand,
      ]);
      expectTypeOf(result).toEqualTypeOf<
        Flag<boolean, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        default: false,
        negation: negation,
      });
      expect(flag.is(result, "boolean")).toBe(true);
    });
  });

  describe("number", () => {
    const name = "timeout";
    const shorthand = "t";
    const description = "Request timeout ms";
    const _default = 5000;

    test("no shorthand", () => {
      const result = flag(name, description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<number, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("shorthand", () => {
      const result = flag([name, shorthand], description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<number, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, no shorthand", () => {
      const result = flags(name, description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<number, typeof name, { multiple: true; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, shorthand", () => {
      const result = flags([name, shorthand], description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<number, typeof name, { multiple: true; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });
  });

  describe("string-enum", () => {
    const name = "env";
    const shorthand = "e";
    const description = "Deploy target";
    const options: ("production" | "staging")[] = ["production", "staging"];
    const _default: (typeof options)[number] = "staging";

    test("no shorthand, no default", () => {
      const result = flag(name, description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        options: options,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("no shorthand, default", () => {
      const result = flag(name, description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("shorthand, no default", () => {
      const result = flag([name, shorthand], description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        options: options,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("shorthand, default", () => {
      const result = flag([name, shorthand], description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, no shorthand, no default", () => {
      const result = flags(name, description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, no shorthand, default", () => {
      const result = flags(name, description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, shorthand, no default", () => {
      const result = flags([name, shorthand], description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, shorthand, default", () => {
      const result = flags([name, shorthand], description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, no shorthand, array default", () => {
      const seeds: (typeof options)[number][] = ["production", "staging"];
      const result = flags(name, description, options, seeds);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
        default: seeds,
      });
    });

    test("multi, shorthand, array default", () => {
      const seeds: (typeof options)[number][] = ["production", "staging"];
      const result = flags([name, shorthand], description, options, seeds);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
        default: seeds,
      });
    });
  });

  describe("number-enum", () => {
    const name = "workers";
    const shorthand = "w";
    const description = "Thread count";
    const options: (1 | 2 | 4 | 8)[] = [1, 2, 4, 8];
    const _default: (typeof options)[number] = 4;

    test("no shorthand, no default", () => {
      const result = flag(name, description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        options: options,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("no shorthand, default", () => {
      const result = flag(name, description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("shorthand, no default", () => {
      const result = flag([name, shorthand], description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        options: options,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("shorthand, default", () => {
      const result = flag([name, shorthand], description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: false; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, no shorthand, no default", () => {
      const result = flags(name, description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, no shorthand, default", () => {
      const result = flags(name, description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, shorthand, no default", () => {
      const result = flags([name, shorthand], description, options);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: false }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, shorthand, default", () => {
      const result = flags([name, shorthand], description, options, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
        default: _default,
      });
      expect(flag.is(result, "number")).toBe(true);
    });

    test("multi, no shorthand, array default", () => {
      const seeds: (typeof options)[number][] = [1, 2];
      const result = flags(name, description, options, seeds);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        options: options,
        default: seeds,
      });
    });

    test("multi, shorthand, array default", () => {
      const seeds: (typeof options)[number][] = [1, 2];
      const result = flags([name, shorthand], description, options, seeds);
      expectTypeOf(result).toEqualTypeOf<
        Flag<
          (typeof options)[number],
          typeof name,
          { multiple: true; default: true }
        >
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        options: options,
        default: seeds,
      });
    });
  });

  describe("string", () => {
    const name = "output";
    const shorthand = "o";
    const description = "Output file path";
    const _default = "./dist";

    test("no shorthand, no default", () => {
      const result = flag(name, description);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: false; default: false }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("no shorthand, default", () => {
      const result = flag(name, description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: false,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("shorthand, no default", () => {
      const result = flag([name, shorthand], description);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: false; default: false }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("shorthand, default", () => {
      const result = flag([name, shorthand], description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: false; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: false,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, no shorthand, no default", () => {
      const result = flags(name, description);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: true; default: false }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, no shorthand, default", () => {
      const result = flags(name, description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: true; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        multiple: true,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, shorthand, no default", () => {
      const result = flags([name, shorthand], description);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: true; default: false }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
      });
      expect(flag.is(result, "string")).toBe(true);
    });

    test("multi, shorthand, default", () => {
      const result = flags([name, shorthand], description, _default);
      expectTypeOf(result).toEqualTypeOf<
        Flag<string, typeof name, { multiple: true; default: true }>
      >();
      expect(result).toEqual({
        longform: name,
        description: description,
        shorthand: shorthand,
        multiple: true,
        default: _default,
      });
      expect(flag.is(result, "string")).toBe(true);
    });
  });
});
