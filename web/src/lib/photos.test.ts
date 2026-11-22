import { describe, expect, it } from "vitest";

import { fitWithin } from "./photos";

describe("fitWithin", () => {
  it("brings an iPhone photo to 1024 pixels on its long side", () => {
    expect(fitWithin(3024, 4032)).toEqual({ width: 768, height: 1024 });
    expect(fitWithin(4032, 3024)).toEqual({ width: 1024, height: 768 });
  });

  it("never enlarges a small picture", () => {
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
  });
});
