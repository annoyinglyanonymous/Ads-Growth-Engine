/* The routes. One per page URL, matching ui.PAGES: a page URL serves this
 * shell and the page asks /api<the same path> for its data. */
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AdPage } from "./pages/AdPage";
import { AnglesPage } from "./pages/AnglesPage";
import { AskPage } from "./pages/AskPage";
import { BriefPage } from "./pages/BriefPage";
import { CreativePage } from "./pages/CreativePage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SuggestionsPage } from "./pages/SuggestionsPage";
import { WhyPage } from "./pages/WhyPage";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/brief" element={<BriefPage />} />
          <Route path="/why" element={<WhyPage />} />
          <Route path="/creative" element={<CreativePage />} />
          <Route path="/angles" element={<AnglesPage />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="/suggestions" element={<SuggestionsPage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/ad/:adKey" element={<AdPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
