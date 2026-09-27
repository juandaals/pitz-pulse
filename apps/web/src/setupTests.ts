/** Vitest setup: jest-dom matchers + DOM cleanup after every test (Spec 05 §8). */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});
