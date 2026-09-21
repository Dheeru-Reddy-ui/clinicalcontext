import { redirect } from "next/navigation";

/** Members moved into Admin (Phase 10). */
export default function MembersRedirect() {
  redirect("/app/admin");
}
