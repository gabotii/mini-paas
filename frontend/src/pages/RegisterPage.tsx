import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button } from "../components/Button";
import { AuthLayout } from "../layouts/AuthLayout";
import { passwordProblems } from "../lib/validation";
import { ApiError } from "../services/api";
import { useAuth } from "../stores/auth";

export function RegisterPage() {
  const register = useAuth((s) => s.register);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const local: Record<string, string> = {};
    if (!name.trim()) local.name = "Name is required";
    if (!/^\S+@\S+\.\S+$/.test(email)) local.email = "Enter a valid email";
    const weak = passwordProblems(password);
    if (weak) local.password = weak;
    setErrors(local);
    if (Object.keys(local).length) return;

    setBusy(true);
    try {
      await register(name.trim(), email, password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors();
        setErrors(Object.keys(fields).length ? fields : { form: err.message });
      } else {
        setErrors({ form: "Registration failed" });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Create your account" subtitle="Deploy your first app in a couple of minutes.">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <label>
          <span className="label">Name</span>
          <input
            className="input"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {errors.name && <p className="field-error">{errors.name}</p>}
        </label>
        <label>
          <span className="label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {errors.email && <p className="field-error">{errors.email}</p>}
        </label>
        <label>
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {errors.password ? (
            <p className="field-error">{errors.password}</p>
          ) : (
            <p className="hint">10+ characters mixing letters, digits and symbols.</p>
          )}
        </label>
        {errors.form && (
          <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
            {errors.form}
          </p>
        )}
        <Button variant="primary" type="submit" loading={busy} className="w-full">
          Create account
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted">
        Already registered?{" "}
        <Link to="/login" className="text-accent hover:underline">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
