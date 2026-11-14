/**
 * The app's routes and providers: public pages, auth screens and the signed-in planning flow.
 *
 * Purpose: One route table decides who sees what: the landing page and auth screens are public
 * (signed-in users skip the auth pages), and every planning screen needs a session and an
 * organization and renders inside the shell, where a page error stays inside the page so
 * navigation keeps working.
 * How it fits: Loaded by `main.tsx` once the configuration is valid; wires `QueryClientProvider`,
 * the router, `AuthProvider`, the route guards, `OrgProvider`, `Shell` and every screen;
 * layer: config.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from 'react-router'
import { queryClient } from './lib/queryClient'
import AuthProvider from './auth/AuthProvider'
import { PublicOnly, RequireAuth } from './auth/guards'
import OrgProvider from './org/OrgProvider'
import Shell from './components/Shell'
import { NotFound, RouteError } from './components/ErrorPages'
import ToastHost from './components/Toast'
import Login from './screens/Login'
import Signup from './screens/Signup'
import CheckEmail from './screens/CheckEmail'
import ForgotPassword from './screens/ForgotPassword'
import ResetPassword from './screens/ResetPassword'
import AuthCallback from './screens/AuthCallback'
import AuthConfirm from './screens/AuthConfirm'
import LandingPage from './screens/LandingPage'
import Dashboard from './screens/Dashboard'
import CreateChooser from './screens/CreateChooser'
import CreateProject from './screens/CreateProject'
import CreateFromUpload from './screens/CreateFromUpload'
import AIProjectIntake from './screens/AIProjectIntake'
import ScopeReview from './screens/ScopeReview'
import WBSBuilder from './screens/WBSBuilder'
import Constraints from './screens/Constraints'
import OptimizationResults from './screens/OptimizationResults'
import ChangesVersions from './screens/ChangesVersions'

/** Provides the session to every route and mounts the app-wide toast once. */
function RootLayout() {
  return (
    <AuthProvider>
      <Outlet />
      <ToastHost />
    </AuthProvider>
  )
}

/** `/register` is the design's sign-up URL; keep the query (e.g. `?next=`) when forwarding. */
function RegisterRedirect() {
  const { search } = useLocation()
  return <Navigate to={{ pathname: '/signup', search }} replace />
}

/** The signed-in frame: resolves the organization, then renders the shell around the screen. */
function AppLayout() {
  return (
    <OrgProvider>
      <Shell>
        <Outlet />
      </Shell>
    </OrgProvider>
  )
}

const router = createBrowserRouter([
  {
    element: <RootLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <LandingPage /> },
      {
        element: <PublicOnly />,
        children: [
          { path: 'login', element: <Login /> },
          { path: 'signup', element: <Signup /> },
          { path: 'check-email', element: <CheckEmail /> },
          { path: 'forgot-password', element: <ForgotPassword /> },
        ],
      },
      { path: 'register', element: <RegisterRedirect /> },
      { path: 'reset-password', element: <ResetPassword /> },
      { path: 'auth/callback', element: <AuthCallback /> },
      { path: 'auth/confirm', element: <AuthConfirm /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppLayout />,
            children: [
              {
                // Errors inside a page render within the shell so navigation stays usable.
                errorElement: <RouteError inline />,
                children: [
                  { path: 'dashboard', element: <Dashboard /> },
                  { path: 'projects/new', element: <CreateChooser /> },
                  { path: 'projects/new/form', element: <CreateProject /> },
                  { path: 'projects/new/upload', element: <CreateFromUpload /> },
                  {
                    path: 'projects/:projectId',
                    children: [
                      { index: true, element: <Navigate to="intake" replace /> },
                      { path: 'intake', element: <AIProjectIntake /> },
                      { path: 'scope', element: <ScopeReview /> },
                      { path: 'wbs', element: <WBSBuilder /> },
                      { path: 'constraints', element: <Constraints /> },
                      { path: 'optimization', element: <OptimizationResults /> },
                      { path: 'changes', element: <ChangesVersions /> },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
])

/** The whole app: the query client around the router. */
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
