import { type FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { Button } from "../components/Button";
import { AuthLayout } from "../layouts/AuthLayout";
import { ApiError } from "../services/api";
import { useAuth } from "../stores/auth";

export function LoginPage() {
  const login = useAuth((s) => s.login);
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : "/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Welcome back" subtitle="Log in to manage your deployments.">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <label>
          <span className="label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label>
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
            {error}
          </p>
        )}
        <Button variant="primary" type="submit" loading={busy} className="w-full">
          Log in
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted">
        No account?{" "}
        <Link to="/register" className="text-accent hover:underline">
          Create one
        </Link>
      </p>
    </AuthLayout>
  );
}
