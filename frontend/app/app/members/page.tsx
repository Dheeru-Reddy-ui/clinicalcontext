import { redirect } from "next/navigation";

/** Members moved into Admin (Phase 10), and Admin into Settings. */
export default function MembersRedirect() {
  redirect("/app/settings?section=members");
}
