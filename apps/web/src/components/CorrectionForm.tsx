/**
 * Enum selects, resumen, requiere_info ⇄ pregunta_seguimiento and reason, with Confirm and Save
 * (Spec 05 §3, §5, §8). Confirm sends only `author` (empty diff = confirmation, D-per Spec 05
 * §5); Save sends only the fields that changed from `initial`, plus `author` and `reason` when
 * given. Turning `requiere_info` off clears `pregunta_seguimiento` to `null` so the diff never
 * carries stale question text (Spec 05 §7); the last-typed text is kept in `savedQuestion` so
 * turning it back on restores it instead of leaving the field blank. A 422's `fields` with
 * `loc: ["body", "<field>"]` render next to that field; `loc: ["body"]` (cross-field rules) stay
 * above the form. A 409 (the row is no longer `classified`, Spec 05 §5/§6) bubbles up via
 * `onConflict` instead of being shown inline, since the caller returns to the list for that case.
 */
import { useState } from "react";
import { areaValues, categoriaValues, idiomaValues, prioridadValues } from "../api/schema";
import { ApiError, patchRequest } from "../api/client";
import type { Area, Categoria, Idioma, Item, PatchBody, Prioridad } from "../api/client";
import { validateCorrection } from "../validation";
import type { FieldError } from "../api/client";

export interface CorrectionFormInitial {
  categoria: Categoria;
  prioridad: Prioridad;
  area_sugerida: Area;
  idioma: Idioma;
  resumen: string;
  requiere_info: boolean;
  pregunta_seguimiento: string | null;
}

export interface CorrectionFormProps {
  apiKey: string;
  requestId: string;
  author: string;
  initial: CorrectionFormInitial;
  onSuccess: (item: Item) => void;
  onConflict: (message: string) => void;
  onUnauthorized: () => void;
}

const CORRECTABLE_FIELDS = [
  "categoria",
  "prioridad",
  "area_sugerida",
  "idioma",
  "resumen",
  "requiere_info",
  "pregunta_seguimiento",
] as const;

// Fields the form has a control for; a field-level error for any other name (e.g. `author`, which
// has no input here) falls back to the above-the-form list instead of being silently dropped.
const RENDERABLE_FIELDS = [...CORRECTABLE_FIELDS, "reason"] as const;

function computeDiff(
  form: CorrectionFormInitial,
  initial: CorrectionFormInitial,
): Partial<CorrectionFormInitial> {
  const diff: Partial<CorrectionFormInitial> = {};
  for (const field of CORRECTABLE_FIELDS) {
    if (form[field] !== initial[field]) {
      (diff as Record<string, unknown>)[field] = form[field];
    }
  }
  return diff;
}

function isErrorFor(error: FieldError, field: string): boolean {
  return error.loc.length > 1 && error.loc[1] === field;
}

function errorsFor(errors: FieldError[], field: string): FieldError[] {
  return errors.filter((error) => isErrorFor(error, field));
}

function aboveFormErrors(errors: FieldError[]): FieldError[] {
  return errors.filter((error) => !RENDERABLE_FIELDS.some((field) => isErrorFor(error, field)));
}

function FieldErrorList({ errors }: { errors: FieldError[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="field-errors inline">
      {errors.map((fieldError, index) => (
        <li key={index}>{fieldError.msg}</li>
      ))}
    </ul>
  );
}

export function CorrectionForm({
  apiKey,
  requestId,
  author,
  initial,
  onSuccess,
  onConflict,
  onUnauthorized,
}: CorrectionFormProps) {
  const [form, setForm] = useState<CorrectionFormInitial>(initial);
  const [savedQuestion, setSavedQuestion] = useState<string | null>(initial.pregunta_seguimiento);
  const [reason, setReason] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleRequiereInfoChange(checked: boolean) {
    if (checked) {
      setForm((prev) => ({ ...prev, requiere_info: true, pregunta_seguimiento: savedQuestion }));
      return;
    }
    if (form.pregunta_seguimiento) {
      setSavedQuestion(form.pregunta_seguimiento);
    }
    setForm((prev) => ({ ...prev, requiere_info: false, pregunta_seguimiento: null }));
  }

  function handleQuestionChange(value: string) {
    setForm((prev) => ({ ...prev, pregunta_seguimiento: value }));
    setSavedQuestion(value);
  }

  async function submit(body: PatchBody) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const item = await patchRequest(apiKey, requestId, body);
      onSuccess(item);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 401) {
          onUnauthorized();
          return;
        }
        if (error.status === 409) {
          onConflict(error.message);
          return;
        }
        if (error.status === 422) {
          setFieldErrors(error.fields ?? []);
          setSubmitting(false);
          return;
        }
        setSubmitError(error.message);
      } else {
        setSubmitError("unexpected error");
      }
      setSubmitting(false);
      return;
    }
    setSubmitting(false);
  }

  function handleConfirm() {
    setFieldErrors([]);
    submit({ author });
  }

  function handleSave() {
    const errors = validateCorrection(
      {
        resumen: form.resumen,
        requiere_info: form.requiere_info,
        pregunta_seguimiento: form.pregunta_seguimiento,
      },
      author,
    );
    if (errors.length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors([]);
    const diff = computeDiff(form, initial);
    const body: PatchBody = { author, ...diff };
    if (reason.trim()) {
      body.reason = reason.trim();
    }
    submit(body);
  }

  const topErrors = aboveFormErrors(fieldErrors);

  return (
    <div className="correction-form">
      {topErrors.length > 0 && (
        <ul className="field-errors">
          {topErrors.map((fieldError, index) => (
            <li key={index}>{fieldError.msg}</li>
          ))}
        </ul>
      )}
      {submitError && <p className="form-error">{submitError}</p>}

      <label>
        Categoria
        <select
          value={form.categoria}
          onChange={(e) => setForm((prev) => ({ ...prev, categoria: e.target.value as Categoria }))}
        >
          {categoriaValues.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <FieldErrorList errors={errorsFor(fieldErrors, "categoria")} />
      </label>
      <label>
        Prioridad
        <select
          value={form.prioridad}
          onChange={(e) => setForm((prev) => ({ ...prev, prioridad: e.target.value as Prioridad }))}
        >
          {prioridadValues.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <FieldErrorList errors={errorsFor(fieldErrors, "prioridad")} />
      </label>
      <label>
        Area sugerida
        <select
          value={form.area_sugerida}
          onChange={(e) =>
            setForm((prev) => ({ ...prev, area_sugerida: e.target.value as Area }))
          }
        >
          {areaValues.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <FieldErrorList errors={errorsFor(fieldErrors, "area_sugerida")} />
      </label>
      <label>
        Idioma
        <select
          value={form.idioma}
          onChange={(e) => setForm((prev) => ({ ...prev, idioma: e.target.value as Idioma }))}
        >
          {idiomaValues.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <FieldErrorList errors={errorsFor(fieldErrors, "idioma")} />
      </label>
      <label>
        Resumen
        <textarea
          value={form.resumen}
          onChange={(e) => setForm((prev) => ({ ...prev, resumen: e.target.value }))}
        />
        <FieldErrorList errors={errorsFor(fieldErrors, "resumen")} />
      </label>
      <label>
        <input
          type="checkbox"
          checked={form.requiere_info}
          onChange={(e) => handleRequiereInfoChange(e.target.checked)}
        />
        Requiere info
        <FieldErrorList errors={errorsFor(fieldErrors, "requiere_info")} />
      </label>
      <label>
        Pregunta de seguimiento
        <textarea
          value={form.pregunta_seguimiento ?? ""}
          disabled={!form.requiere_info}
          onChange={(e) => handleQuestionChange(e.target.value)}
        />
        <FieldErrorList errors={errorsFor(fieldErrors, "pregunta_seguimiento")} />
      </label>
      <label>
        Reason (optional)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
        <FieldErrorList errors={errorsFor(fieldErrors, "reason")} />
      </label>

      <div className="actions">
        <button type="button" disabled={submitting} onClick={handleConfirm}>
          Confirm
        </button>
        <button type="button" disabled={submitting} onClick={handleSave}>
          Save
        </button>
      </div>
    </div>
  );
}
