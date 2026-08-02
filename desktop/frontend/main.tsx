import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../../app/globals.css";
import "../../app/details.css";
import Page from "../../app/page";


const root = document.getElementById("root");
if (!root) throw new Error("desktop_root_not_found");

createRoot(root).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
