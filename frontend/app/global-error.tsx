"use client";

/**
 * Last-resort boundary: replaces the root layout when it throws. Must render
 * its own <html>/<body> and cannot rely on providers, so it is deliberately
 * plain and self-contained.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          fontFamily: "system-ui, sans-serif",
          background: "#fbfbfc",
          color: "#1f2430",
        }}
      >
        <main role="alert" style={{ maxWidth: 420, padding: 24, textAlign: "center" }}>
          <h1 style={{ fontSize: 18, margin: "0 0 8px" }}>ClinicalContext could not load</h1>
          <p style={{ fontSize: 14, color: "#5b6270", margin: "0 0 16px" }}>
            Reloading usually fixes this. If it keeps happening, quote the reference below.
          </p>
          {error.digest && (
            <p style={{ fontFamily: "monospace", fontSize: 11, color: "#5b6270" }}>ref {error.digest}</p>
          )}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 8,
              padding: "8px 14px",
              borderRadius: 6,
              border: "1px solid #c9ced8",
              background: "#2f5b8c",
              color: "#fff",
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </main>
      </body>
    </html>
  );
}
