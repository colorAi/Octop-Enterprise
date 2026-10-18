import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FakeWorker = {
  state: string;
  postMessage: ReturnType<typeof vi.fn>;
  addEventListener: (type: string, fn: () => void) => void;
};

function mockRegistration(opts: {
  waiting?: FakeWorker | null;
  installing?: FakeWorker | null;
  controller?: { scriptURL: string } | null;
}) {
  const registrationListeners = new Map<string, Array<() => void>>();
  const swListeners = new Map<string, Array<() => void>>();
  const registration = {
    waiting: opts.waiting ?? null,
    installing: opts.installing ?? null,
    addEventListener: (type: string, fn: () => void) => {
      const list = registrationListeners.get(type) ?? [];
      list.push(fn);
      registrationListeners.set(type, list);
    },
    emit: (type: string) => {
      for (const fn of registrationListeners.get(type) ?? []) fn();
    },
    update: vi.fn(),
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      register: vi.fn().mockResolvedValue(registration),
      getRegistrations: vi.fn().mockResolvedValue([]),
      controller: opts.controller ?? null,
      addEventListener: (
        type: string,
        fn: () => void,
        _options?: { once?: boolean },
      ) => {
        const list = swListeners.get(type) ?? [];
        list.push(fn);
        swListeners.set(type, list);
      },
      removeEventListener: (type: string, fn: () => void) => {
        const list = swListeners.get(type) ?? [];
        swListeners.set(
          type,
          list.filter((listener) => listener !== fn),
        );
      },
      emit: (type: string) => {
        for (const fn of [...(swListeners.get(type) ?? [])]) fn();
      },
    },
  });
  return registration;
}

describe("registerProductionSW", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("activates a waiting worker when the page is uncontrolled", async () => {
    const waiting: FakeWorker = {
      state: "installed",
      postMessage: vi.fn(),
      addEventListener: vi.fn(),
    };
    mockRegistration({ waiting, controller: null });

    const { registerProductionSW } = await import("./sw-register");
    await registerProductionSW();

    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
    expect(navigator.serviceWorker.register).toHaveBeenCalledWith("/sw.js", {
      scope: "/",
      updateViaCache: "none",
    });
  });

  it("prompts when a waiting worker exists and the page is controlled", async () => {
    const waiting: FakeWorker = {
      state: "installed",
      postMessage: vi.fn(),
      addEventListener: vi.fn(),
    };
    mockRegistration({
      waiting,
      controller: { scriptURL: "https://x/sw.js" },
    });
    const onReady = vi.fn();
    window.addEventListener("pwa:update-ready", onReady);

    const { registerProductionSW } = await import("./sw-register");
    await registerProductionSW();

    expect(waiting.postMessage).not.toHaveBeenCalled();
    expect(onReady).toHaveBeenCalledOnce();
  });
});

describe("applyUpdate", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.stubGlobal("location", {
      ...window.location,
      reload: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reloads immediately when no waiting worker is pending", async () => {
    mockRegistration({ waiting: null, controller: null });
    const { applyUpdate } = await import("./sw-register");

    await applyUpdate();

    expect(window.location.reload).toHaveBeenCalledOnce();
  });

  it("waits for controllerchange before reloading", async () => {
    const waiting: FakeWorker = {
      state: "installed",
      postMessage: vi.fn(),
      addEventListener: vi.fn(),
    };
    mockRegistration({
      waiting,
      controller: { scriptURL: "https://x/sw.js" },
    });

    const { applyUpdate, registerProductionSW } = await import("./sw-register");
    await registerProductionSW();

    const pending = applyUpdate();
    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
    expect(window.location.reload).not.toHaveBeenCalled();

    (
      navigator.serviceWorker as unknown as { emit: (type: string) => void }
    ).emit("controllerchange");
    await pending;

    expect(window.location.reload).toHaveBeenCalledOnce();
  });

  it("reloads after timeout if controllerchange never fires", async () => {
    const waiting: FakeWorker = {
      state: "installed",
      postMessage: vi.fn(),
      addEventListener: vi.fn(),
    };
    mockRegistration({
      waiting,
      controller: { scriptURL: "https://x/sw.js" },
    });

    const { applyUpdate, registerProductionSW } = await import("./sw-register");
    await registerProductionSW();

    const pending = applyUpdate();
    expect(window.location.reload).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(3000);
    await pending;

    expect(window.location.reload).toHaveBeenCalledOnce();
  });
});

describe("company desktop workers", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("DEV", false);
    vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
  });
  afterEach(() => {
    delete window.__octopRuntime;
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each(["bridge", "bundle"])(
    "cleans old workers without enabling updates (%s)",
    async (mode) => {
      if (mode === "bridge")
        Object.defineProperty(window, "__octopRuntime", {
          value: { version: 1 },
          configurable: true,
        });
      if (mode === "bundle") vi.stubEnv("VITE_DESKTOP_BUNDLE", "1");
      const waiting: FakeWorker = {
        state: "installed",
        postMessage: vi.fn(),
        addEventListener: vi.fn(),
      };
      mockRegistration({
        waiting,
        controller: { scriptURL: "http://company/sw.js" },
      });
      const unregister = vi.fn().mockResolvedValue(true);
      vi.mocked(navigator.serviceWorker.getRegistrations).mockResolvedValue([
        { unregister } as unknown as ServiceWorkerRegistration,
      ]);
      const removeCache = vi.fn().mockResolvedValue(true);
      vi.stubGlobal("caches", {
        keys: vi.fn().mockResolvedValue(["workbox-precache"]),
        delete: removeCache,
      });
      const { registerSW, registerProductionSW, applyUpdate } = await import(
        "./sw-register"
      );
      await registerSW();
      await registerProductionSW();
      await applyUpdate();
      expect(unregister).toHaveBeenCalledOnce();
      expect(removeCache).toHaveBeenCalledWith("workbox-precache");
      expect(navigator.serviceWorker.register).not.toHaveBeenCalled();
      expect(waiting.postMessage).not.toHaveBeenCalled();
      expect(window.location.reload).not.toHaveBeenCalled();
    },
  );
});
