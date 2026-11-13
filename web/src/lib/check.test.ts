// The shared correction vectors, run against the browser checker (Python runs them too).

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { check } from "./check";

const directory = join(__dirname, "../../../tests/correction");

for (const file of readdirSync(directory).filter((name) => name.endsWith(".json")).sort()) {
  describe(file, () => {
    for (const vector of JSON.parse(readFileSync(join(directory, file), "utf8"))) {
      it(vector.name, () => {
        const result = check(vector.mode, vector.answer, vector.response, {
          variants: vector.variants,
          tolerance: vector.tolerance,
        });
        expect(result).toBe(vector.expected);
      });
    }
  });
}
