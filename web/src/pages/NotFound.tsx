import { Link } from "react-router";
import { EmptyState } from "@/components/ui";

export default function NotFound() {
  return (
    <EmptyState title="This page does not exist" action={<Link className="text-accent underline" to="/">Back to the dashboard</Link>}>
      Check the address, or use the menu.
    </EmptyState>
  );
}
