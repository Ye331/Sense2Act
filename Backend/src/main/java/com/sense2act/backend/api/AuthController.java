package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.ErrorCode;
import com.sense2act.backend.config.JwtTokens;
import com.sense2act.backend.domain.user.User;
import com.sense2act.backend.domain.user.UserService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;

/** 认证:邮箱密码换 JWT。 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    public record TokenRequest(
            @NotBlank(message = "email 不能为空") String email,
            @NotBlank(message = "password 不能为空") String password) {
    }

    public record UserInfo(String id, String email, String name, String role) {
    }

    public record TokenResponse(String token, String token_type, long expires_in, UserInfo user) {
    }

    private final UserService userService;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokens jwtTokens;

    public AuthController(UserService userService, PasswordEncoder passwordEncoder, JwtTokens jwtTokens) {
        this.userService = userService;
        this.passwordEncoder = passwordEncoder;
        this.jwtTokens = jwtTokens;
    }

    public static UserInfo toUserInfo(User user) {
        return new UserInfo(user.getId(), user.getEmail(), user.getName(), user.getRole());
    }

    @PostMapping("/token")
    public ApiResponse<TokenResponse> token(@Valid @RequestBody TokenRequest request) {
        User user = userService.findByEmail(request.email());
        if (user == null || !passwordEncoder.matches(request.password(), user.getPasswordHash())) {
            throw new BusinessException(ErrorCode.UNAUTHORIZED, "邮箱或密码错误");
        }
        Duration ttl = jwtTokens.ttl();
        return ApiResponse.ok(new TokenResponse(
                jwtTokens.issue(user.getId(), user.getRole(), user.getName()),
                "Bearer", ttl.toSeconds(), toUserInfo(user)));
    }
}
