const express = require('express');
const mongoose = require('mongoose');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const passport = require('passport');
const session = require('express-session');
const NaverStrategy = require('passport-naver').Strategy;
const KakaoStrategy = require('passport-kakao').Strategy;
const cors = require('cors');
const path = require('path');
require('dotenv').config();

const app = express();

// 미들웨어 설정
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(session({
    secret: process.env.SESSION_SECRET || 'your-secret-key',
    resave: false,
    saveUninitialized: false
}));
app.use(passport.initialize());
app.use(passport.session());

// MongoDB 연결
mongoose.connect(process.env.MONGODB_URI || 'mongodb://localhost/ef_db', {
    useNewUrlParser: true,
    useUnifiedTopology: true
}).then(() => console.log('MongoDB Connected'))
  .catch(err => console.error('MongoDB Connection Error:', err));

// 사용자 스키마
const UserSchema = new mongoose.Schema({
    email: { type: String, required: true, unique: true },
    password: { type: String },
    naverId: { type: String },
    kakaoId: { type: String },
    provider: { type: String } // 'local', 'naver', 'kakao'
});

const User = mongoose.model('User', UserSchema);

// Passport 직렬화
passport.serializeUser((user, done) => {
    done(null, user.id);
});

passport.deserializeUser((id, done) => {
    User.findById(id, (err, user) => {
        done(err, user);
    });
});

// 네이버 로그인 전략
passport.use(new NaverStrategy({
    clientID: process.env.NAVER_CLIENT_ID,
    clientSecret: process.env.NAVER_CLIENT_SECRET,
    callbackURL: '/auth/naver/callback'
}, async (accessToken, refreshToken, profile, done) => {
    try {
        let user = await User.findOne({ naverId: profile.id });
        if (!user) {
            user = new User({
                email: profile.emails[0].value,
                naverId: profile.id,
                provider: 'naver'
            });
            await user.save();
        }
        return done(null, user);
    } catch (err) {
        return done(err);
    }
}));

// 카카오 로그인 전략
passport.use(new KakaoStrategy({
    clientID: process.env.KAKAO_REST_API_KEY,
    callbackURL: '/auth/kakao/callback'
}, async (accessToken, refreshToken, profile, done) => {
    try {
        let user = await User.findOne({ kakaoId: profile.id });
        if (!user) {
            user = new User({
                email: profile._json.kakao_account.email,
                kakaoId: profile.id,
                provider: 'kakao'
            });
            await user.save();
        }
        return done(null, user);
    } catch (err) {
        return done(err);
    }
}));

// 라우트 설정
app.get('/test', (req, res) => {
    res.send('Server is running!');
});

app.get('/auth/naver', passport.authenticate('naver'));
app.get('/auth/naver/callback', passport.authenticate('naver', {
    successRedirect: '/index.html#simulation',
    failureRedirect: '/login.html'
}));

app.get('/auth/kakao', passport.authenticate('kakao'));
app.get('/auth/kakao/callback', passport.authenticate('kakao', {
    successRedirect: '/index.html#simulation',
    failureRedirect: '/login.html'
}));

// 회원가입 API
app.post('/api/signup', async (req, res) => {
    const { email, password } = req.body;
    try {
        let user = await User.findOne({ email });
        if (user) {
            return res.status(400).json({ message: '이미 존재하는 이메일입니다.' });
        }
        const hashedPassword = await bcrypt.hash(password, 10);
        user = new User({
            email,
            password: hashedPassword,
            provider: 'local'
        });
        await user.save();
        const token = jwt.sign({ id: user._id }, process.env.JWT_SECRET || 'your-jwt-secret', { expiresIn: '1h' });
        res.json({ token });
    } catch (err) {
        res.status(500).json({ message: '서버 오류' });
    }
});

// 로그인 API
app.post('/api/login', async (req, res) => {
    const { email, password } = req.body;
    try {
        const user = await User.findOne({ email });
        if (!user || user.provider !== 'local') {
            return res.status(400).json({ message: '이메일 또는 비밀번호가 잘못되었습니다.' });
        }
        const isMatch = await bcrypt.compare(password, user.password);
        if (!isMatch) {
            return res.status(400).json({ message: '이메일 또는 비밀번호가 잘못되었습니다.' });
        }
        const token = jwt.sign({ id: user._id }, process.env.JWT_SECRET || 'your-jwt-secret', { expiresIn: '1h' });
        res.json({ token });
    } catch (err) {
        res.status(500).json({ message: '서버 오류' });
    }
});

// 정적 파일 제공
app.use(express.static(__dirname));

// 서버 시작
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
    console.log(`Access at http://localhost:${PORT}`);
});