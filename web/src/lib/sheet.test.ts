import { describe, expect, it } from "vitest";

import { basedOn, formatLabel, quoteSource } from "./sheet";

const labels = {
  "lesson:2026-11-04:HI-GE": "ton cours du mardi 4/11",
  "doc:3:p86": "ton manuel p. 86",
  "doc:3:p87": "ton manuel p. 87",
};

describe("sheet", () => {
  it("names each format", () => {
    expect(formatLabel("lesson_recap")).toBe("Cours en clair");
    expect(formatLabel("quick_quiz")).toBe("Interro flash");
    expect(formatLabel("something_new")).toBe("Fiche");
  });

  it("says where the sheet comes from", () => {
    expect(basedOn({ sources: ["lesson:2026-11-04:HI-GE"], source_labels: labels })).toBe("D'après ton cours du mardi 4/11");
    expect(basedOn({ sources: ["lesson:2026-11-04:HI-GE", "doc:3:p86", "doc:3:p87"], source_labels: labels })).toBe(
      "D'après ton cours du mardi 4/11, ton manuel p. 86 et ton manuel p. 87",
    );
    expect(basedOn({ sources: ["lesson:x"] })).toBe(""); // a sheet written by hand
  });

  it("says where a quote comes from", () => {
    expect(quoteSource({ source_labels: labels }, { quote: { source_id: "doc:3:p86", excerpt: "…" } })).toBe("ton manuel p. 86");
    expect(quoteSource({ source_labels: labels }, { quote: null })).toBe("ton cours");
  });
});
