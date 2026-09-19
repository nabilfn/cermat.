"use client";

import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "./api";

type State<T> = { key: string | null; data: T | null; error: string };

/**
 * Load data for a key. Loading is derived (the loaded key differs from the
 * requested one), so no setState runs synchronously inside the effect.
 */
export function useResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<State<T>>({ key: null, data: null, error: "" });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => setState({ key, data, error: "" }))
      .catch((error) => {
        if (!controller.signal.aborted) setState((current) => ({ key, data: current.data, error: errorMessage(error) }));
      });
    return () => controller.abort();
    // `load` is expected to be derived from `key`; reloads are driven by key/nonce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return {
    data: state.data,
    error: state.key === key ? state.error : "",
    loading: key !== null && state.key !== key,
    reload,
    setData: (data: T) => setState({ key, data, error: "" }),
  };
}
