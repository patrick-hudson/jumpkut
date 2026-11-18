//! Byte-exact `json.dumps(..., ensure_ascii=False, indent=2)` output, so files
//! written by the Rust app are identical to the Python app's.

/// Python's `repr(float)`: shortest round-trip digits, scientific notation
/// when the decimal exponent is < -4 or >= 16, `.0` for integral values.
pub fn float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    // `{:e}` gives the shortest round-trip digits, e.g. "1.7e20", "-1e-7".
    let formatted = format!("{value:e}");
    let (mantissa, exponent) = formatted.split_once('e').expect("LowerExp has an exponent");
    let exponent: i32 = exponent.parse().expect("integer exponent");
    let (sign, mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |m| ("-", m));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if !(-4..16).contains(&exponent) {
        let mut out = String::from(sign);
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exponent < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exponent.abs()));
        return out;
    }
    let point = exponent + 1; // digits before the decimal point
    let mut out = String::from(sign);
    if point <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', (-point) as usize));
        out.push_str(&digits);
    } else if point as usize >= digits.len() {
        out.push_str(&digits);
        out.extend(std::iter::repeat_n('0', point as usize - digits.len()));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..point as usize]);
        out.push('.');
        out.push_str(&digits[point as usize..]);
    }
    out
}

/// A JSON string as Python writes it with `ensure_ascii=False` (only `"`, `\`
/// and control characters are escaped; serde_json uses the same rules).
pub fn string(value: &str) -> String {
    serde_json::to_string(value).expect("strings always serialize")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_match_python_repr() {
        for (value, python) in [
            (1700000000.125, "1700000000.125"),
            (1e-7, "1e-07"),
            (1.7e20, "1.7e+20"),
            (1731648000.0, "1731648000.0"),
            (0.5, "0.5"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1e16, "1e+16"),
            (1234567890123456.0, "1234567890123456.0"),
            (-2.5, "-2.5"),
        ] {
            assert_eq!(float(value), python, "{value}");
        }
    }
}
