// SPDX-License-Identifier: AGPL-3.0-or-later
import '@testing-library/jest-dom';

// jsdom has no ResizeObserver; components observing element size
// (e.g. the explorer table's scroll nudges) need this no-op stand-in.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? (ResizeObserverStub as unknown as typeof ResizeObserver);
