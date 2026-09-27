import { describe, expect, it } from "vitest";
import { type CorrectionInput, validateCorrection, wordCount } from "./validation";

const baseForm: CorrectionInput = {
  resumen: "un resumen valido de la solicitud",
  requiere_info: false,
  pregunta_seguimiento: null,
};

describe("wordCount", () => {
  it("matches the server's word_count on the shared vector (double space + newline)", () => {
    expect(wordCount("uno  dos\ntres")).toBe(3);
  });

  it("counts a single word", () => {
    expect(wordCount("solo")).toBe(1);
  });

  it("counts zero words for blank text", () => {
    expect(wordCount("   \n  ")).toBe(0);
  });
});

describe("validateCorrection — resumen word/char limit", () => {
  it("accepts a resumen within 1-20 words", () => {
    expect(validateCorrection(baseForm, "ana")).toEqual([]);
  });

  it("rejects a resumen with more than 20 words", () => {
    const resumen = Array.from({ length: 21 }, (_, i) => `palabra${i}`).join(" ");
    const errors = validateCorrection({ ...baseForm, resumen }, "ana");
    expect(errors).toEqual([{ loc: ["body"], msg: "resumen must have 1-20 words, has 21" }]);
  });

  it("rejects an empty resumen", () => {
    const errors = validateCorrection({ ...baseForm, resumen: "   " }, "ana");
    expect(errors).toEqual([{ loc: ["body"], msg: "resumen must have 1-20 words, has 0" }]);
  });

  it("rejects a resumen over 200 characters even with few words", () => {
    const resumen = "a".repeat(201);
    const errors = validateCorrection({ ...baseForm, resumen }, "ana");
    expect(errors).toEqual([{ loc: ["body"], msg: "resumen must have at most 200 characters" }]);
  });
});

describe("validateCorrection — pregunta_seguimiento rules", () => {
  it("requires a question when requiere_info is true", () => {
    const errors = validateCorrection(
      { ...baseForm, requiere_info: true, pregunta_seguimiento: null },
      "ana",
    );
    expect(errors).toEqual([
      { loc: ["body"], msg: "pregunta_seguimiento is required when requiere_info is true" },
    ]);
  });

  it("rejects a blank question when requiere_info is true", () => {
    const errors = validateCorrection(
      { ...baseForm, requiere_info: true, pregunta_seguimiento: "   " },
      "ana",
    );
    expect(errors).toEqual([
      { loc: ["body"], msg: "pregunta_seguimiento is required when requiere_info is true" },
    ]);
  });

  it("rejects a question over 300 characters", () => {
    const errors = validateCorrection(
      { ...baseForm, requiere_info: true, pregunta_seguimiento: "a".repeat(301) },
      "ana",
    );
    expect(errors).toEqual([
      { loc: ["body"], msg: "pregunta_seguimiento must have at most 300 characters" },
    ]);
  });

  it("accepts a question when requiere_info is true", () => {
    const errors = validateCorrection(
      { ...baseForm, requiere_info: true, pregunta_seguimiento: "cual es tu duda?" },
      "ana",
    );
    expect(errors).toEqual([]);
  });

  it("rejects a question when requiere_info is false", () => {
    const errors = validateCorrection(
      { ...baseForm, requiere_info: false, pregunta_seguimiento: "no deberia estar" },
      "ana",
    );
    expect(errors).toEqual([
      { loc: ["body"], msg: "pregunta_seguimiento must be null when requiere_info is false" },
    ]);
  });

  it("accepts a null question when requiere_info is false", () => {
    expect(validateCorrection({ ...baseForm, requiere_info: false, pregunta_seguimiento: null }, "ana")).toEqual(
      [],
    );
  });
});

describe("validateCorrection — author required 1-100", () => {
  it("rejects an empty author", () => {
    const errors = validateCorrection(baseForm, "");
    expect(errors).toEqual([{ loc: ["body", "author"], msg: "author must have 1-100 characters" }]);
  });

  it("rejects a whitespace-only author", () => {
    const errors = validateCorrection(baseForm, "   ");
    expect(errors).toEqual([{ loc: ["body", "author"], msg: "author must have 1-100 characters" }]);
  });

  it("rejects an author over 100 characters", () => {
    const errors = validateCorrection(baseForm, "a".repeat(101));
    expect(errors).toEqual([{ loc: ["body", "author"], msg: "author must have 1-100 characters" }]);
  });

  it("accepts an author with exactly 1 and exactly 100 characters", () => {
    expect(validateCorrection(baseForm, "a")).toEqual([]);
    expect(validateCorrection(baseForm, "a".repeat(100))).toEqual([]);
  });
});
