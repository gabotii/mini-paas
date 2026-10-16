export function validateArchive(file: File, maxMb: number): string | null {
  if (!file.name.toLowerCase().endsWith(".zip")) return "Choose a .zip archive";
  if (file.size === 0) return "The file is empty";
  if (file.size > maxMb * 1024 * 1024) return `Archives are limited to ${maxMb} MB`;
  return null;
}

/** Mirrors the backend password policy so users get instant feedback. */
export function passwordProblems(password: string): string | null {
  if (password.length < 10) return "At least 10 characters";
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length;
  if (classes < 3) return "Mix at least three of: lowercase, uppercase, digits, symbols";
  return null;
}

export const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,39}$/;
export const ENV_KEY_RE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
