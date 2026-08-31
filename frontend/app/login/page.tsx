import { AuthForm } from "@/components/AuthForm";

export default function LoginPage() {
  return (
    <div className="py-10">
      <AuthForm mode="login" />
      <p className="mx-auto mt-4 max-w-sm text-center text-sm text-muted">
        No account yet? <a href="/register" className="underline">Register</a>
      </p>
    </div>
  );
}
