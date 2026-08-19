import { NextRequest, NextResponse } from "next/server";
import { submitHumanDecision } from "../../../../lib/server-adk";
import type { HumanRequest } from "../../../../lib/adk";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      userId,
      sessionId,
      decision,
      humanRequest
    } = body ?? {};

    if (
      !userId ||
      !sessionId ||
      !decision ||
      !humanRequest
    ) {
      return NextResponse.json(
        {
          error:
            "userId, sessionId, decision and humanRequest are required."
        },
        { status: 400 }
      );
    }

    return NextResponse.json(
      await submitHumanDecision(
        String(userId),
        String(sessionId),
        humanRequest as HumanRequest,
        String(decision)
      )
    );
  } catch (error) {
    console.error(error);

    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Unable to continue migration."
      },
      { status: 500 }
    );
  }
}
