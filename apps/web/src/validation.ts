/**
 * Client mirror of the correction contract rules (Spec 05 §3, schema.py `_check_rules` and
 * api_models.py `PatchBody._author`). The server stays authoritative: this only lets the form
 * show errors before submitting, using the same `{loc, msg}` shape as the API's `fields`.
 */
import type { FieldError } from "./api/client";

export const MAX_SUMMARY_WORDS = 20;
export const MAX_SUMMARY_CHARS = 200;
export const MAX_QUESTION_CHARS = 300;
export const MIN_AUTHOR_CHARS = 1;
export const MAX_AUTHOR_CHARS = 100;

export interface CorrectionInput {
  resumen: string;
  requiere_info: boolean;
  pregunta_seguimiento: string | null;
}

/** Same rule as the server's `word_count` (Python `str.split()` with no separator). */
export function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function validateCorrection(form: CorrectionInput, author: string): FieldError[] {
  const errors: FieldError[] = [];

  // resumen and the requiere_info/pregunta_seguimiento relationship are both raised from the
  // server's single model-level validator, so both arrive with loc ["body"] (Spec 05 §7).
  const words = wordCount(form.resumen);
  if (words < 1 || words > MAX_SUMMARY_WORDS) {
    errors.push({
      loc: ["body"],
      msg: `resumen must have 1-${MAX_SUMMARY_WORDS} words, has ${words}`,
    });
  } else if (form.resumen.length > MAX_SUMMARY_CHARS) {
    errors.push({ loc: ["body"], msg: `resumen must have at most ${MAX_SUMMARY_CHARS} characters` });
  }

  const question = form.pregunta_seguimiento;
  if (form.requiere_info) {
    if (question === null || question.trim() === "") {
      errors.push({
        loc: ["body"],
        msg: "pregunta_seguimiento is required when requiere_info is true",
      });
    } else if (question.length > MAX_QUESTION_CHARS) {
      errors.push({
        loc: ["body"],
        msg: `pregunta_seguimiento must have at most ${MAX_QUESTION_CHARS} characters`,
      });
    }
  } else if (question !== null && question !== "") {
    errors.push({ loc: ["body"], msg: "pregunta_seguimiento must be null when requiere_info is false" });
  }

  // author is a field-level validator server-side, so it keeps its own loc.
  const trimmedAuthor = author.trim();
  if (trimmedAuthor.length < MIN_AUTHOR_CHARS || trimmedAuthor.length > MAX_AUTHOR_CHARS) {
    errors.push({
      loc: ["body", "author"],
      msg: `author must have ${MIN_AUTHOR_CHARS}-${MAX_AUTHOR_CHARS} characters`,
    });
  }

  return errors;
}
