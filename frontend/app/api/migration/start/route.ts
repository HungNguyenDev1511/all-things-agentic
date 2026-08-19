import { NextResponse } from "next/server";
import { startDemo } from "../../../../lib/server-adk";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  try {
    return NextResponse.json(await startDemo());
  } catch (error) {
    console.error(error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unable to start migration." },
      { status: 500 }
    );
  }
}
