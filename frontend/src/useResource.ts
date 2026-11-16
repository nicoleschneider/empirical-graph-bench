import { useCallback, useEffect, useState } from "react";
import { get } from "./api";

export type Resource<T> = {
  data: T | null;
  error: string | null;
  reload: () => void;
};

export function useResource<T>(path: string | null, version = 0): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [local, setLocal] = useState(0);
  const reload = useCallback(() => setLocal((v) => v + 1), []);

  useEffect(() => {
    let active = true;
    if (path === null) {
      setData(null);
      return;
    }
    get<T>(path)
      .then((value) => {
        if (active) {
          setData(value);
          setError(null);
        }
      })
      .catch((reason: Error) => {
        if (active) {
          setError(reason.message);
        }
      });
    return () => {
      active = false;
    };
  }, [path, version, local]);

  return { data, error, reload };
}
