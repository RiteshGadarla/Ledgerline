import { AuthForm } from "@/components/AuthForm";

export default function RegisterPage() {
  return (
    <div className="py-10">
      <AuthForm mode="register" />
      <p className="mx-auto mt-4 max-w-sm text-center text-sm text-muted">
        Already have an account? <a href="/login" className="underline">Sign in</a>
      </p>
    </div>
  );
}
