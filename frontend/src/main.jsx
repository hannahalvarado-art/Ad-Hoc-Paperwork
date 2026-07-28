import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
// Tailwind/shadcn tokens first so the hand-written design system in
// styles.css keeps the last word on anything both files set.
import "./index.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
