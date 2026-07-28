import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
// AG Grid module registration is a startup side effect, done here so it cannot
// depend on which grid happens to be imported first. See lib/agGrid.js.
import "./lib/agGrid.js";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
