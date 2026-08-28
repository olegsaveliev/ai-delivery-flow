import { useEffect, useState } from "react";
import { api } from "./api/client";

export default function App() {
  const [status, setStatus] = useState<string>("checking…");

  useEffect(() => {
    api
      .health()
      .then((r) => setStatus(r.status))
      .catch((e) => setStatus(`error: ${e.message}`));
  }, []);

  return (
    <main className="app">
      <h1>AI Delivery Flow</h1>
      <p>
        Backend status: <strong>{status}</strong>
      </p>
    </main>
  );
}
