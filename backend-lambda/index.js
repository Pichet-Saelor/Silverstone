const { DynamoDBClient } = require("@aws-sdk/client-dynamodb");
const {
  DynamoDBDocumentClient,
  ScanCommand,
  GetCommand,
  PutCommand,
  UpdateCommand,
  QueryCommand,
} = require("@aws-sdk/lib-dynamodb");
const crypto = require("crypto");

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}));

const EVENTS_TABLE = process.env.EVENTS_TABLE || "Events_Table";
const BOOKINGS_TABLE = process.env.BOOKINGS_TABLE || "Bookings_Table";
const STRIPE_SECRET_KEY = process.env.STRIPE_SECRET_KEY || "";
const HOLD_DURATION_MS = 10 * 60 * 1000;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Content-Type": "application/json",
};

function response(statusCode, body) {
  return {
    statusCode,
    headers: CORS_HEADERS,
    body: JSON.stringify(body),
  };
}

function getUserIdFromEvent(event) {
  const claims = event.requestContext?.authorizer?.claims;
  if (claims && (claims.sub || claims.email)) {
    return claims.sub || claims.email;
  }
  const auth = event.headers?.Authorization || event.headers?.authorization || "";
  if (auth.startsWith("Bearer ")) {
    try {
      const payload = JSON.parse(Buffer.from(auth.split(".")[1], "base64").toString("utf8"));
      return payload.sub || payload.email || null;
    } catch (_) {}
  }
  return null;
}

async function createStripePromptPay(amountTHB, bookingId, email) {
  const piParams = new URLSearchParams();
  piParams.append("amount", String(Math.round(amountTHB * 100)));
  piParams.append("currency", "thb");
  piParams.append("metadata[booking_id]", bookingId);
  piParams.append("automatic_payment_methods[enabled]", "true");
  piParams.append("automatic_payment_methods[allow_redirects]", "never");

  const piRes = await fetch("https://api.stripe.com/v1/payment_intents", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: piParams.toString(),
  });
  const pi = await piRes.json();
  if (!piRes.ok) throw new Error(pi.error?.message || "Stripe PaymentIntent failed");

  const confirmParams = new URLSearchParams();
  confirmParams.append("payment_method_data[type]", "promptpay");
  confirmParams.append("payment_method_data[billing_details][email]", email || "customer@silverstone.app");

  const confirmRes = await fetch(`https://api.stripe.com/v1/payment_intents/${pi.id}/confirm`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: confirmParams.toString(),
  });
  const confirmed = await confirmRes.json();
  if (!confirmRes.ok) throw new Error(confirmed.error?.message || "Stripe Confirm failed");

  const qrBlock = confirmed.next_action?.promptpay_display_qr_code || {};
  return {
    payment_intent_id: confirmed.id,
    qr_image_url: qrBlock.image_url_png || qrBlock.image_url_svg || "",
    hosted_instructions_url: qrBlock.hosted_instructions_url || "",
  };
}

async function cancelStripePaymentIntent(paymentIntentId) {
  if (!STRIPE_SECRET_KEY.startsWith("sk_") || !paymentIntentId) {
    return;
  }
  try {
    await fetch(`https://api.stripe.com/v1/payment_intents/${paymentIntentId}/cancel`, {
      method: "POST",
      headers: { Authorization: `Bearer ${STRIPE_SECRET_KEY}` },
    });
  } catch (_) {}
}

async function inspectStripePaymentStatus(paymentIntentId) {
  if (!STRIPE_SECRET_KEY.startsWith("sk_") || !paymentIntentId) {
    return "PENDING";
  }
  const res = await fetch(`https://api.stripe.com/v1/payment_intents/${paymentIntentId}`, {
    method: "GET",
    headers: { Authorization: `Bearer ${STRIPE_SECRET_KEY}` },
  });
  if (!res.ok) return "PENDING";
  const data = await res.json();
  if (data.status === "succeeded") return "PAID";
  if (data.status === "canceled" || data.last_payment_error) return "FAILED";
  return "PENDING";
}

async function markBookingAsPaid(bookingId, transRef) {
  await ddb.send(
    new UpdateCommand({
      TableName: BOOKINGS_TABLE,
      Key: { booking_id: bookingId },
      UpdateExpression: "SET #st = :paid, trans_ref = :ref, paid_at = :at",
      ConditionExpression: "#st = :pending",
      ExpressionAttributeNames: { "#st": "status" },
      ExpressionAttributeValues: {
        ":paid": "PAID",
        ":pending": "PENDING",
        ":ref": transRef,
        ":at": new Date().toISOString(),
      },
    })
  );
}

async function releaseBookingAndRestoreStock(booking, newStatus) {
  try {
    await ddb.send(
      new UpdateCommand({
        TableName: BOOKINGS_TABLE,
        Key: { booking_id: booking.booking_id },
        UpdateExpression: "SET #st = :newSt, released_at = :at",
        ConditionExpression: "#st = :pending",
        ExpressionAttributeNames: { "#st": "status" },
        ExpressionAttributeValues: {
          ":newSt": newStatus,
          ":pending": "PENDING",
          ":at": new Date().toISOString(),
        },
      })
    );

    await ddb.send(
      new UpdateCommand({
        TableName: EVENTS_TABLE,
        Key: { event_id: booking.event_id },
        UpdateExpression: "SET available_tickets = available_tickets + :q",
        ExpressionAttributeValues: { ":q": Number(booking.quantity || 1) },
      })
    );

    await cancelStripePaymentIntent(booking.payment_intent_id);
    return true;
  } catch (err) {
    return false;
  }
}

exports.handler = async (event) => {
  const method = event.httpMethod || event.requestContext?.http?.method || "GET";
  const path = event.path || event.rawPath || "";

  if (method === "OPTIONS") {
    return response(200, { ok: true });
  }

  try {
    if (method === "GET" && path.endsWith("/events")) {
      const result = await ddb.send(new ScanCommand({ TableName: EVENTS_TABLE }));
      const events = (result.Items || []).sort((a, b) => a.event_id.localeCompare(b.event_id));
      return response(200, { events });
    }

    if (method === "POST" && path.endsWith("/bookings")) {
      const userId = getUserIdFromEvent(event);
      if (!userId) return response(401, { message: "Unauthorized: กรุณาเข้าสู่ระบบ" });

      const body = JSON.parse(event.body || "{}");
      const eventId = body.event_id;
      const userEmail = body.email || "customer@silverstone.app";
      const quantity = Math.min(5, Math.max(1, Number(body.quantity || 1)));

      if (!eventId) return response(400, { message: "กรุณาระบุ event_id" });

      const evtRes = await ddb.send(new GetCommand({ TableName: EVENTS_TABLE, Key: { event_id: eventId } }));
      const evt = evtRes.Item;
      if (!evt) return response(404, { message: "ไม่พบข้อมูลงานอีเวนต์" });
      if (Number(evt.available_tickets) < quantity) {
        return response(400, { message: "จำนวนบัตรคงเหลือไม่เพียงพอ" });
      }

      if (!STRIPE_SECRET_KEY || !STRIPE_SECRET_KEY.startsWith("sk_")) {
        return response(500, { message: "ยังไม่ได้กำหนดค่า STRIPE_SECRET_KEY ในระบบ API" });
      }

      await ddb.send(
        new UpdateCommand({
          TableName: EVENTS_TABLE,
          Key: { event_id: eventId },
          UpdateExpression: "SET available_tickets = available_tickets - :q",
          ConditionExpression: "available_tickets >= :q",
          ExpressionAttributeValues: { ":q": quantity },
        })
      );

      const totalAmount = Number(evt.price) * quantity;
      const bookingId = "BK-" + crypto.randomBytes(4).toString("hex").toUpperCase();
      const now = Date.now();
      const expiresAt = new Date(now + HOLD_DURATION_MS).toISOString();

      let paymentData;
      try {
        paymentData = await createStripePromptPay(totalAmount, bookingId, userEmail);
      } catch (stripeErr) {
        await ddb.send(
          new UpdateCommand({
            TableName: EVENTS_TABLE,
            Key: { event_id: eventId },
            UpdateExpression: "SET available_tickets = available_tickets + :q",
            ExpressionAttributeValues: { ":q": quantity },
          })
        );
        throw stripeErr;
      }

      const booking = {
        booking_id: bookingId,
        user_id: userId,
        event_id: eventId,
        event_title: evt.title,
        event_date: evt.date,
        venue: evt.venue,
        quantity,
        total_amount: totalAmount,
        payment_intent_id: paymentData.payment_intent_id,
        qr_image_url: paymentData.qr_image_url,
        hosted_instructions_url: paymentData.hosted_instructions_url,
        status: "PENDING",
        created_at: new Date(now).toISOString(),
        expires_at: expiresAt,
      };

      await ddb.send(new PutCommand({ TableName: BOOKINGS_TABLE, Item: booking }));
      return response(201, { booking });
    }

    if (method === "GET" && path.endsWith("/my-bookings")) {
      const userId = getUserIdFromEvent(event);
      if (!userId) return response(401, { message: "Unauthorized" });

      const qRes = await ddb.send(
        new QueryCommand({
          TableName: BOOKINGS_TABLE,
          IndexName: "user_id-index",
          KeyConditionExpression: "user_id = :uid",
          ExpressionAttributeValues: { ":uid": userId },
        })
      );

      const items = qRes.Items || [];
      const nowMs = Date.now();

      // รองรับ fallback จำลองการชำระเงินผ่าน query parameter ?simulate_id=BK-xxx
      const simId = event.queryStringParameters?.simulate_id;
      if (simId) {
        const simBk = items.find(b => b.booking_id === simId && b.status === "PENDING");
        if (simBk) {
          const simRef = simBk.payment_intent_id || `SIM_${simBk.booking_id}`;
          await markBookingAsPaid(simBk.booking_id, simRef);
          simBk.status = "PAID";
          simBk.trans_ref = simRef;
          simBk.paid_at = new Date().toISOString();
        }
      }

      for (const bk of items) {
        if (bk.status === "PENDING") {
          const stripeStatus = await inspectStripePaymentStatus(bk.payment_intent_id);

          if (stripeStatus === "PAID") {
            await markBookingAsPaid(bk.booking_id, bk.payment_intent_id);
            bk.status = "PAID";
            bk.trans_ref = bk.payment_intent_id;
          } else if (stripeStatus === "FAILED") {
            const released = await releaseBookingAndRestoreStock(bk, "FAILED");
            if (released) bk.status = "FAILED";
          } else if (bk.expires_at && new Date(bk.expires_at).getTime() <= nowMs) {
            const released = await releaseBookingAndRestoreStock(bk, "EXPIRED");
            if (released) bk.status = "EXPIRED";
          }
        }
      }

      const bookings = items.sort((a, b) =>
        (b.created_at || "").localeCompare(a.created_at || "")
      );
      return response(200, { bookings });
    }

    if (method === "POST" && path.endsWith("/cancel-booking")) {
      const userId = getUserIdFromEvent(event);
      if (!userId) return response(401, { message: "Unauthorized: กรุณาเข้าสู่ระบบ" });

      const body = JSON.parse(event.body || "{}");
      if (!body.booking_id) return response(400, { message: "กรุณาระบุ booking_id" });

      const bkRes = await ddb.send(
        new GetCommand({ TableName: BOOKINGS_TABLE, Key: { booking_id: body.booking_id } })
      );
      const bk = bkRes.Item;
      if (!bk || bk.user_id !== userId) {
        return response(404, { message: "ไม่พบรายการจอง" });
      }
      if (bk.status !== "PENDING") {
        return response(400, { message: "รายการนี้ไม่สามารถยกเลิกได้" });
      }

      await releaseBookingAndRestoreStock(bk, "CANCELED");
      return response(200, { ok: true, booking_id: bk.booking_id, status: "CANCELED" });
    }

    if (method === "POST" && path.endsWith("/simulate-payment")) {
      const userId = getUserIdFromEvent(event);
      if (!userId) return response(401, { message: "Unauthorized: กรุณาเข้าสู่ระบบ" });

      const body = JSON.parse(event.body || "{}");
      if (!body.booking_id) return response(400, { message: "กรุณาระบุ booking_id" });

      const bkRes = await ddb.send(
        new GetCommand({ TableName: BOOKINGS_TABLE, Key: { booking_id: body.booking_id } })
      );
      const bk = bkRes.Item;
      if (!bk || bk.user_id !== userId) {
        return response(404, { message: "ไม่พบรายการจอง" });
      }
      if (bk.status !== "PENDING") {
        return response(400, { message: `รายการนี้อยู่ในสถานะ ${bk.status} แล้ว` });
      }

      const transRef = bk.payment_intent_id || `SIM_${bk.booking_id}`;
      await markBookingAsPaid(bk.booking_id, transRef);
      return response(200, { ok: true, booking_id: bk.booking_id, status: "PAID", message: "จำลองการชำระเงินสำเร็จ" });
    }

    return response(404, { message: `Route not found: ${method} ${path}` });
  } catch (err) {
    console.error("Lambda Error:", err);
    return response(500, { message: err.message || "Internal Server Error" });
  }
};
