import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

serve(async (req) => {
  const { license_key } = await req.json()

  const supabaseClient = createClient(
    Deno.env.get('SUPABASE_URL') ?? '',
    Deno.env.get('SUPABASE_ANON_KEY') ?? ''
  )

  const { data, error } = await supabaseClient
    .from('client_licenses')
    .select('status')
    .eq('license_key', license_key)
    .single()

  if (error || !data) {
    return new Response(
      JSON.stringify({ status: 'unpaid', message: 'Invalid or missing license' }),
      { headers: { "Content-Type": "application/json" } },
    )
  }

  return new Response(
    JSON.stringify({ status: data.status }),
    { headers: { "Content-Type": "application/json" } },
  )
})