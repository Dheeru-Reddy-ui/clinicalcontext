import { redirect } from "next/navigation";

/**
 * Admin moved into Settings: members, public sharing, API keys, the private
 * library and the plan are its Workspace sections now. Old links land on
 * members, where Admin used to open.
 */
export default function AdminRedirect() {
  redirect("/app/settings?section=members");
}
