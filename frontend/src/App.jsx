import React, { Suspense, lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { AuthProvider, GuestOnly, RequireAuth, UpgradeListener } from "./auth/AuthContext.jsx";
import { SiteLayout } from "./layout/Layout.jsx";
import { Home, NotFound, Pricing, Privacy, Terms } from "./pages/Public.jsx";
import { ForgotPassword, Login, Register, ResetPassword } from "./pages/AuthPages.jsx";
import { Account, BillingReturn, MockGcash } from "./pages/BillingPages.jsx";
import "./pages/pages.css";

// The workspace pulls in three.js (~1 MB); keep it out of the landing-page bundle.
const Workspace = lazy(() => import("./pages/Workspace.jsx"));

const Boot = () => <div className="gh-boot" aria-busy="true" />;

export default function App() {
  return (
    <AuthProvider>
      <Suspense fallback={<Boot />}>
        <Routes>
          {/* public + account pages share the site chrome */}
          <Route element={<SiteLayout />}>
            <Route index element={<Home />} />
            <Route path="pricing" element={<Pricing />} />
            <Route path="terms" element={<Terms />} />
            <Route path="privacy" element={<Privacy />} />
            <Route path="account" element={<RequireAuth><Account /></RequireAuth>} />
            <Route path="billing/return" element={<RequireAuth><BillingReturn /></RequireAuth>} />
            <Route path="*" element={<NotFound />} />
          </Route>

          {/* full-screen auth screens */}
          <Route path="login" element={<GuestOnly><Login /></GuestOnly>} />
          <Route path="register" element={<GuestOnly><Register /></GuestOnly>} />
          <Route path="forgot-password" element={<ForgotPassword />} />
          <Route path="reset-password" element={<ResetPassword />} />

          {/* sandbox GCash (the backend 404s this unless PAYMENT_PROVIDER=mock) */}
          <Route path="pay/mock/:paymentId" element={<RequireAuth><MockGcash /></RequireAuth>} />

          {/* the workspace: FEA at /app, CFD at /app/cfd/..., CAD at /app/cad */}
          <Route path="app/*" element={<RequireAuth><Workspace /></RequireAuth>} />
        </Routes>
      </Suspense>
      <UpgradeListener />
    </AuthProvider>
  );
}
