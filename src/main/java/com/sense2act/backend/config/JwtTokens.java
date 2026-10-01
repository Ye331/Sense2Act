package com.sense2act.backend.config;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.OctetSequenceKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;

import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;

/** JWT 签发与解码(HS256)。秘密短于 32 字符直接拒绝创建——弱密钥不允许上线。 */
public class JwtTokens {

    public static final String CLAIM_ROLE = "role";
    public static final String CLAIM_NAME = "name";

    private final JwtEncoder encoder;
    private final JwtDecoder decoder;
    private final Duration ttl;

    public JwtTokens(String secret, Duration ttl) {
        byte[] bytes = secret.getBytes(StandardCharsets.UTF_8);
        if (bytes.length < 32) {
            throw new IllegalStateException(
                    "JWT_SECRET 至少 32 字符(当前 " + bytes.length + ")。生产必须注入强随机密钥");
        }
        SecretKey key = new SecretKeySpec(bytes, "HmacSHA256");
        try {
            var jwk = new OctetSequenceKey.Builder(bytes).algorithm(JWSAlgorithm.HS256).build();
            this.encoder = new NimbusJwtEncoder(new ImmutableJWKSet<>(new JWKSet(jwk)));
        } catch (Exception e) {
            throw new IllegalStateException("JWT 密钥初始化失败", e);
        }
        this.decoder = NimbusJwtDecoder.withSecretKey(key).macAlgorithm(MacAlgorithm.HS256).build();
        this.ttl = ttl;
    }

    public String issue(String userId, String role, String name) {
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("sense2act")
                .subject(userId)
                .claim(CLAIM_ROLE, role)
                .claim(CLAIM_NAME, name)
                .issuedAt(now)
                .expiresAt(now.plus(ttl))
                .build();
        return encoder.encode(JwtEncoderParameters.from(
                JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
    }

    public JwtDecoder decoder() {
        return decoder;
    }

    public Duration ttl() {
        return ttl;
    }
}
