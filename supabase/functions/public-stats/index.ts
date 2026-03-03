import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );

    const [urls, users, projects] = await Promise.all([
      supabase.from("url_mappings").select("*", { count: "exact", head: true }),
      supabase.from("user_profiles").select("*", { count: "exact", head: true }),
      supabase.from("migration_sessions").select("*", { count: "exact", head: true }),
    ]);

    if (urls.error || users.error || projects.error) {
      const err = urls.error || users.error || projects.error;
      return new Response(
        JSON.stringify({ error: "Database query failed", detail: err.message }),
        {
          status: 500,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    return new Response(
      JSON.stringify({
        urls_matched: urls.count ?? 0,
        total_users: users.count ?? 0,
        total_projects: projects.count ?? 0,
      }),
      {
        headers: {
          ...corsHeaders,
          "Content-Type": "application/json",
          "Cache-Control": "public, max-age=3600",
        },
      }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "Internal server error" }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  }
});
