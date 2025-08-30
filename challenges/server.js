import express from 'express';
import cors from 'cors';
import fetch from 'node-fetch';

const app = express();
app.use(cors());
app.use(express.json());

const SECRET = process.env.TURNSTILE_SECRET || '';

app.post('/verify', async (req, res) => {
  try {
    const token = req.body.token;
    if (!token) return res.status(400).json({ok:false, error:'missing token'});
    const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({secret: SECRET, response: token})
    });
    const data = await r.json();
    res.json({ok: data.success === true, raw: data});
  } catch(e) {
    res.status(500).json({ok:false, error: e.message});
  }
});

app.listen(8787, ()=>console.log('Turnstile verifier on :8787'));
