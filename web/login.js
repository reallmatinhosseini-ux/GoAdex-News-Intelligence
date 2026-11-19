/* Admin sign-in. Credentials go only to this server over JSON; the session is an HttpOnly cookie. */
const form = document.getElementById("login");
const err = document.getElementById("err");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  err.textContent = "";
  const btn = form.querySelector("button");
  const email = form.email.value.trim(), password = form.password.value;
  if (!email || !password) { err.textContent = "Enter your email and password."; return; }
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetch("/api/auth/login", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify({ email, password }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "Sign-in failed.");
    location.href = "/app";
  } catch (x) {
    err.textContent = x.message;
    form.password.value = "";
    btn.disabled = false; btn.textContent = "Sign in";
  }
});
