"use client";

import { Mic, MicOff, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Check the microphone before a voice session opens.
 *
 * "The website is unable to record my voice" has three causes that look the
 * same from the outside — the browser blocked the microphone, the wrong
 * device is selected, or the server cannot run voice — and a session that
 * opens and hears nothing explains none of them. This shows, before anything
 * connects, whether the browser will give the page a microphone, which one,
 * and a live level so a person can see their own voice move the bar.
 *
 * The chosen device is remembered on this browser and handed to the session.
 * The test stream is released before the session opens its own, so two
 * streams never compete for one device.
 */

const DEVICE_KEY = "cc.voice.micDeviceId";

type Permission = "granted" | "denied" | "prompt" | "unknown";

export function readSavedMicId(): string | undefined {
  try {
    return window.localStorage.getItem(DEVICE_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

/**
 * What to ask the browser for when voice starts: the microphone chosen in
 * Settings (as a preference — if it has been unplugged the default is used),
 * with the browser's cleanup that speech recognition does best with.
 */
export function micConstraints(): MediaTrackConstraints {
  const saved = readSavedMicId();
  return {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    ...(saved ? { deviceId: { ideal: saved } } : {}),
  };
}

function saveMicId(id: string): void {
  try {
    window.localStorage.setItem(DEVICE_KEY, id);
  } catch {
    /* private mode: the choice lasts for this page only */
  }
}

/** A plain-language reason for a getUserMedia failure. */
export function describeMicError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "The browser has blocked the microphone for this site. Click the icon at the left of the address bar, set Microphone to Allow, then reload the page.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Plug one in, or choose a different one below.";
    case "NotReadableError":
    case "AbortError":
      return "The microphone is in use by another app or tab. Close it there, then try again.";
    default:
      return error instanceof Error ? error.message : "The microphone could not be opened.";
  }
}

export function MicCheck({
  deviceId,
  onDeviceChange,
  className,
}: {
  deviceId: string | undefined;
  onDeviceChange: (id: string | undefined) => void;
  className?: string;
}) {
  const [permission, setPermission] = useState<Permission>("unknown");
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [testing, setTesting] = useState(false);
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const frame = useRef<number | null>(null);

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const all = await navigator.mediaDevices.enumerateDevices();
    setDevices(all.filter((d) => d.kind === "audioinput" && d.deviceId));
  }, []);

  // Permission state, where the browser will say (Firefox and Safari may not).
  useEffect(() => {
    let status: PermissionStatus | null = null;
    const update = () => status && setPermission(status.state as Permission);
    void navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((s) => {
        status = s;
        update();
        s.addEventListener("change", update);
      })
      .catch(() => setPermission("unknown"));
    void refreshDevices();
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshDevices);
    return () => {
      status?.removeEventListener("change", update);
      navigator.mediaDevices?.removeEventListener?.("devicechange", refreshDevices);
    };
  }, [refreshDevices]);

  const stopTest = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    void context.current?.close();
    context.current = null;
    setTesting(false);
    setLevel(0);
  }, []);

  useEffect(() => stopTest, [stopTest]);

  const startTest = useCallback(async () => {
    setError(null);
    setHeard(false);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser cannot record audio. Use a current Chrome, Edge, Safari or Firefox over HTTPS.");
      return;
    }
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      stream.current = media;
      setPermission("granted");
      // Device labels are only readable once permission is granted.
      await refreshDevices();
      const actual = media.getAudioTracks()[0]?.getSettings().deviceId;
      if (actual && actual !== deviceId) onDeviceChange(actual);

      const ctx = new AudioContext();
      context.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      ctx.createMediaStreamSource(media).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      const tick = () => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const s of samples) sum += s * s;
        const rms = Math.sqrt(sum / samples.length);
        // Speech sits around 0.02–0.2 RMS; map it onto the bar with headroom.
        const shown = Math.min(1, rms * 6);
        setLevel(shown);
        if (shown > 0.12) setHeard(true);
        frame.current = requestAnimationFrame(tick);
      };
      tick();
      setTesting(true);
    } catch (err) {
      stopTest();
      if (err instanceof DOMException && err.name === "NotAllowedError") setPermission("denied");
      setError(describeMicError(err));
    }
  }, [deviceId, onDeviceChange, refreshDevices, stopTest]);

  const choose = (id: string) => {
    saveMicId(id);
    onDeviceChange(id || undefined);
    if (testing) {
      stopTest();
    }
  };

  return (
    <div className={cn("flex flex-col gap-3 rounded-md border bg-background/50 p-3", className)} data-testid="mic-check">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Microphone</span>
        {devices.length > 1 && (
          <select
            aria-label="Choose microphone"
            className="h-8 max-w-64 rounded-md border bg-background px-2 text-sm"
            value={deviceId ?? ""}
            onChange={(e) => choose(e.target.value)}
          >
            {devices.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `Microphone ${i + 1}`}
              </option>
            ))}
          </select>
        )}
        {devices.length === 1 && devices[0]?.label && (
          <span className="text-sm text-muted-foreground">{devices[0].label}</span>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => (testing ? stopTest() : void startTest())}
          data-testid="mic-test"
        >
          {testing ? <MicOff /> : <Mic />} {testing ? "Stop test" : "Test microphone"}
        </Button>
        {permission === "denied" && !testing && (
          <Button type="button" size="sm" variant="ghost" onClick={() => window.location.reload()}>
            <RefreshCw /> Reload after allowing
          </Button>
        )}
      </div>

      {testing && (
        <div className="flex items-center gap-3">
          <div
            className="h-2 flex-1 overflow-hidden rounded-full bg-muted"
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(level * 100)}
          >
            <div
              className={cn("h-full rounded-full transition-[width] duration-75", heard ? "bg-emerald-500" : "bg-primary")}
              style={{ width: `${Math.round(level * 100)}%` }}
            />
          </div>
          <span className="w-44 text-xs text-muted-foreground" role="status" data-testid="mic-heard">
            {heard ? "Your voice is coming through." : "Say something — the bar should move."}
          </span>
        </div>
      )}

      {!testing && !error && permission === "denied" && (
        // Blocked before anyone clicked: say so now, not after a failed test.
        <p className="text-sm text-destructive" role="alert">
          {describeMicError(new DOMException("blocked", "NotAllowedError"))}
        </p>
      )}
      {!testing && !error && permission === "prompt" && (
        <p className="text-xs text-muted-foreground">
          The browser will ask for permission to use the microphone. Choose Allow.
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
