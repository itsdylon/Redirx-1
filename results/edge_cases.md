# Edge Cases

Top failure modes from current benchmark run:

- other: 1517
- wildcard_expansion_ambiguity: 572
- no_prediction: 304
- many_to_one_merges: 279
- soft_404_or_thin_pages: 138

Representative examples:

- other: aws-amplify/docs /cli-legacy/graphql-transformer/storage -> expected /javascript/tools/cli-legacy/storage but predicted /gen1/<platform>/tools/cli-legacy
- wildcard_expansion_ambiguity: hashicorp/terraform-website /docs/providers/ksyun(/index.html) -> expected /providers/kingsoftcloud/ksyun/latest/docs but predicted /providers/linode/linode/latest/docs
- no_prediction: aws-amplify/docs /gen2/how-amplify-works -> expected /react/how-amplify-works but predicted (none)
- many_to_one_merges: aws-amplify/docs /cli/usage/tags -> expected /javascript/tools/cli/project/tags but predicted /gen1/<platform>/tools/cli/<page>
- soft_404_or_thin_pages: aws-amplify/docs /gen2/how-amplify-works -> expected /react/how-amplify-works but predicted /amplify-js/api
